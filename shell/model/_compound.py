from __future__ import annotations

import os
import sys
from typing import override

from ..environment import env
from ..trap import TrapType
from ._base import ShellRunnable, _fork_exec, _wait_child, run
from ._types import IOConfig, ShellResult


class Subshell(ShellRunnable):
    """Execute a runnable in a forked subprocess.

    Args:
        runnable: The command/pipeline to execute in the subshell.
        inherit_traps: If True, synthetic traps (DEBUG, ERR, RETURN, TRACE) are
            inherited from the parent. EXIT is never inherited. Default False
            matches bash default; set True when errtrace/functrace are enabled.
    """

    def __init__(self, runnable: ShellRunnable, *, inherit_traps: bool = False):
        super().__init__()
        self._runnable = runnable
        self._inherit_traps = inherit_traps

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Flush before forking to avoid duplicated output
        sys.stdout.flush()
        sys.stderr.flush()

        if (pid := os.fork()) == 0:
            # Child process - handle trap inheritance
            if self._inherit_traps:
                # Keep synthetic traps but clear EXIT (each subshell has its own)
                env.traps.set(TrapType.EXIT, None)
            else:
                # Default: clear all synthetic traps (bash default behavior)
                env.traps.reset_for_child()
            env.update(self._env_overlay)
            _fork_exec(lambda: self._runnable._exec(actual), fire_exit_trap=True)
        else:
            # Parent process
            return ShellResult(_wait_child(pid))


class Negated(ShellRunnable):
    def __init__(self, runnable: ShellRunnable):
        super().__init__()
        self._runnable = runnable

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        # Merge: instance config takes precedence over passed io
        actual = self._io.merge_over(io)

        # Run and negate the result
        result = run(self._runnable, actual)
        return ~result


class ConditionalChain(ShellRunnable):
    """Execute second command conditionally based on first command's exit code.

    Used by if_success() and if_fail() methods for && and || chaining.

    Args:
        first: The command to run first.
        second: The command to run conditionally.
        on_success: If True, run second on success (&&). If False, run on failure (||).
    """

    def __init__(self, first: ShellRunnable, second: ShellRunnable, on_success: bool):
        super().__init__()
        self._first = first
        self._second = second
        self._on_success = on_success

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        actual = self._io.merge_over(io)

        # Run first command
        result = run(self._first, actual)

        # Check condition: run second if (success and on_success) or (fail and not on_success)
        should_run_second = (result.exit_code == 0) == self._on_success

        if should_run_second:
            return run(self._second, actual)
        else:
            return result


class TracedRunnable(ShellRunnable):
    """Wrapper that prints trace output before executing.

    Used by bash xtrace (-x) and can be used by Python for debugging.

    Trace output goes to sys.stderr, which reflects outer-scope redirections
    (from _redirected context). Per-command redirects don't affect trace
    because trace is written before the command's redirects are applied.
    """

    def __init__(self, inner: ShellRunnable, display_text: str, prefix: str = '+ '):
        super().__init__()
        self._inner = inner
        self._display_text = display_text
        self._prefix = prefix

    @override
    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        print(f'{self._prefix}{self._display_text}', file=sys.stderr)
        sys.stderr.flush()
        # Merge our IO config with passed io so redirects propagate to inner
        actual = self._io.merge_over(io)
        return run(self._inner, actual)
