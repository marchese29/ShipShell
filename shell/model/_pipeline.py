from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast, override

from ..environment import env
from ..trap import TrapType
from ._base import ShellRunnable, _fork_exec, _wait_child
from ._command import InProcessCallable
from ._compound import TracedRunnable
from ._types import IOConfig, ShellResult

if TYPE_CHECKING:
    from ._program import Program


class Pipeline(ShellRunnable):
    """Execute commands connected by pipes.

    All stages are forked -- the parent creates pipes, forks children, and waits.

    Args:
        stages: All commands in the pipeline. Each runs in a forked process.
        pipefail: If True, pipeline fails if any stage fails (not just last).
        inherit_traps: If True, synthetic traps (DEBUG, ERR, RETURN, TRACE) are
            inherited by forked pipeline stages. EXIT is never inherited.
    """

    def __init__(
        self,
        stages: list[ShellRunnable],
        pipefail: bool = False,
        inherit_traps: bool = False,
    ):
        super().__init__()
        self.stages = stages
        self.pipefail = pipefail
        self._inherit_traps = inherit_traps

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Print all traces in order BEFORE forking to ensure deterministic output
        unwrapped: list[ShellRunnable] = []
        for stage in self.stages:
            if isinstance(stage, TracedRunnable):
                print(f'{stage._prefix}{stage._display_text}', file=sys.stderr)
                unwrapped.append(cast(ShellRunnable, stage._inner))
            else:
                unwrapped.append(stage)

        # Flush before forking to avoid duplicated output
        sys.stdout.flush()
        sys.stderr.flush()

        child_pids: list[int] = []
        current_pipe_read: int | None = None

        for i, stage in enumerate(unwrapped):
            is_first = i == 0
            is_last = i == len(unwrapped) - 1

            # Determine stdin for this stage
            stdin_fd = actual.stdin if is_first else current_pipe_read

            # Determine stdout for this stage
            pipe_r: int | None = None
            pipe_w: int | None = None
            if is_last:
                stdout_fd = actual.stdout
            else:
                pipe_r, pipe_w = os.pipe()
                os.set_inheritable(pipe_r, False)
                os.set_inheritable(pipe_w, False)
                stdout_fd = pipe_w

            if (pid := os.fork()) == 0:
                # Child process - handle trap inheritance
                if self._inherit_traps:
                    env.traps.set(TrapType.EXIT, None)
                else:
                    env.traps.reset_for_child()

                # Close read end of output pipe (parent uses it)
                if pipe_r is not None:
                    os.close(pipe_r)

                stage_io = IOConfig(stdin=stdin_fd, stdout=stdout_fd, stderr=actual.stderr)
                _fork_exec(lambda s=stage, sio=stage_io: s._exec(sio), fire_exit_trap=True)
            else:
                # Parent process
                child_pids.append(pid)

                # Close write end of output pipe (child uses it)
                if pipe_w is not None:
                    os.close(pipe_w)

                # Close previous pipe read (child inherited it)
                if current_pipe_read is not None:
                    os.close(current_pipe_read)

                # Advance to next pipe
                current_pipe_read = pipe_r

        # Wait for ALL children and collect exit codes
        exit_codes = [_wait_child(pid) for pid in child_pids]

        # Exit code is from last stage by default
        result = ShellResult(exit_codes[-1] if exit_codes else 0)

        # If pipefail, use first non-zero exit code
        if self.pipefail:
            for code in exit_codes:
                if code != 0:
                    result.exit_code = code
                    break

        return result

    @override
    def __or__(self, value: ShellRunnable | Program | Callable[[], Any]) -> Pipeline:
        from ._program import Program  # noqa: PLC0415

        # Auto-call uncalled Programs
        if isinstance(value, Program):
            value = value()
        # Auto-wrap plain callables
        elif callable(value) and not isinstance(value, ShellRunnable):
            value = InProcessCallable(value)

        # Pipelines flatten into one another
        if isinstance(pipeline := value, Pipeline):
            return Pipeline(
                [*self.stages, *pipeline.stages],
                pipefail=self.pipefail or pipeline.pipefail,
            )
        value = cast(ShellRunnable, value)
        return Pipeline([*self.stages, value], pipefail=self.pipefail)
