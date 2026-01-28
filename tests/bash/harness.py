"""Test harness for bash compatibility testing.

Uses fork-based isolation to run bash code in a clean environment,
then communicates results back via JSON over a pipe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from shell.trap import TrapType

# Prefer homebrew bash (5.x with associative array support) over macOS /bin/bash (3.2)
_HOMEBREW_BASH = '/opt/homebrew/bin/bash'
BASH_PATH = _HOMEBREW_BASH if os.path.exists(_HOMEBREW_BASH) else '/bin/bash'


@dataclass
class CapturedState:
    """State captured from running bash code."""

    stdout: str
    stderr: str
    exit_code: int
    env: dict[str, Any]


def run_isolated(
    code: str,
    setup_env: dict[str, str] | None = None,
    probe_env_vars: list[str] | None = None,
) -> CapturedState:
    """Run bash code in a forked child process with isolation.

    Args:
        code: Bash code to execute
        setup_env: Environment variables to set before running
        probe_env_vars: Environment variable names to capture after running

    Returns:
        CapturedState with stdout, stderr, exit_code, and probed env vars
    """
    setup_env = setup_env or {}
    probe_env_vars = probe_env_vars or []

    # Pipe for JSON results (exit code + env vars)
    result_r, result_w = os.pipe()

    # Pipes for stdout/stderr capture
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    # Flush before forking
    sys.stdout.flush()
    sys.stderr.flush()

    pid = os.fork()
    if pid == 0:
        # === CHILD PROCESS (isolated) ===
        _run_in_child(code, setup_env, probe_env_vars, result_w, stdout_w, stderr_w)
        # _run_in_child calls os._exit(), should never return
    else:
        # === PARENT PROCESS ===
        return _collect_from_child(pid, result_r, stdout_r, stderr_r, result_w, stdout_w, stderr_w)


def _run_in_child(
    code: str,
    setup_env: dict[str, str],
    probe_env_vars: list[str],
    result_w: int,
    stdout_w: int,
    stderr_w: int,
) -> None:
    """Child process: run bash code and exit with the result code.

    The exit code is communicated via the process exit status (waitpid).
    Only probed env vars are written to the result pipe.
    """
    # Redirect stdout/stderr to capture pipes
    os.dup2(stdout_w, 1)
    os.dup2(stderr_w, 2)
    os.close(stdout_w)
    os.close(stderr_w)

    # Reassign sys.stdout/stderr to new file objects wrapping the redirected fds
    # This is necessary because pytest's capture replaces sys.stdout/stderr with
    # its own objects, and after fork those still point to pytest's buffers
    sys.stdout = os.fdopen(1, 'w', buffering=1)
    sys.stderr = os.fdopen(2, 'w', buffering=1)

    # Use the global env directly (we're forked, so changes don't affect parent)
    # This is necessary because resolve_cmd uses the global env for PATH lookup
    from shell.environment import env as global_env

    # Clear and reinitialize the environment
    global_env._env.clear()
    global_env._exported.clear()
    global_env.initialize()

    # Reset trap state inherited from parent
    global_env.traps.reset_for_child()

    # Populate with setup vars
    for key, value in setup_env.items():
        global_env._env[key] = value

    # Import and run bash code with the global env
    from shell.compat.bash import run_bash_code

    exit_code = 0
    try:
        run_bash_code(code, env=global_env)
        exit_code = global_env.last_exit
    except SystemExit as e:
        # Handle exit() builtin
        exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        exit_code = 1
    finally:
        # Fire EXIT trap if set during bash execution
        try:
            global_env.traps.fire(TrapType.EXIT)
        except Exception:
            pass  # Don't let trap failure prevent exit
        global_env.traps.cleanup()

    # Flush output before writing results
    sys.stdout.flush()
    sys.stderr.flush()

    # Write probed env vars to result pipe (only env, not exit_code)
    probed_env = {var: global_env.get(var) for var in probe_env_vars}
    with os.fdopen(result_w, 'w') as f:
        json.dump(probed_env, f)

    # Exit with the actual exit code - parent reads this via waitpid
    os._exit(exit_code)


def _collect_from_child(
    pid: int,
    result_r: int,
    stdout_r: int,
    stderr_r: int,
    result_w: int,
    stdout_w: int,
    stderr_w: int,
) -> CapturedState:
    """Parent process: wait for child and collect results from pipes."""
    # Close write ends (child uses these)
    os.close(result_w)
    os.close(stdout_w)
    os.close(stderr_w)

    # Wait for child and get exit status
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        exit_code = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        exit_code = 128 + os.WTERMSIG(status)
    else:
        exit_code = 1

    # Read captured outputs
    with os.fdopen(stdout_r) as f:
        stdout = f.read()
    with os.fdopen(stderr_r) as f:
        stderr = f.read()
    with os.fdopen(result_r) as f:
        content = f.read()
        probed_env = json.loads(content) if content else {}

    return CapturedState(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        env=probed_env,
    )


def run_bash_reference(
    code: str,
    setup_env: dict[str, str] | None = None,
) -> CapturedState:
    """Run bash code using the system bash for reference comparison.

    Uses a temp script file instead of -c to get proper script semantics
    for LINENO, FUNCNAME, BASH_LINENO, etc.

    Args:
        code: Bash code to execute
        setup_env: Environment variables to set before running

    Returns:
        CapturedState with stdout, stderr, exit_code (env is always empty)
    """
    import tempfile

    setup_env = setup_env or {}

    # Build minimal environment for bash
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': os.environ.get('HOME', '/tmp'),
        **setup_env,
    }

    # Write code to temp file and source it (matches our interpreter semantics)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        result = subprocess.run(
            [BASH_PATH, '-c', f'source "{script_path}"'],
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        os.unlink(script_path)

    return CapturedState(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        env={},  # Can't probe env from subprocess
    )


# === Check Types (for explicit expectations) ===


@dataclass
class Exact:
    """Must match exactly."""
    value: str


@dataclass
class Contains:
    """Must contain all these substrings (order doesn't matter)."""
    substrings: list[str]


@dataclass
class LinesUnordered:
    """Must have exactly these lines, but order doesn't matter."""
    lines: list[str]


@dataclass
class Regex:
    """Must match this regex pattern."""
    pattern: str


@dataclass
class Ignore:
    """Don't check this output."""
    pass


# Union of all check types
Check = Exact | Contains | LinesUnordered | Regex | Ignore


def check(actual: str, expected: Check) -> bool:
    """Check if actual value matches the expected Check.

    Returns True if check passes, False otherwise.
    Use with pytest: assert check(result.stdout, Contains(['hello']))
    """
    match expected:
        case Exact(value):
            return actual == value

        case Contains(substrings):
            return all(s in actual for s in substrings)

        case LinesUnordered(lines):
            actual_lines = set(actual.strip().split('\n')) if actual.strip() else set()
            return actual_lines == set(lines)

        case Regex(pattern):
            return re.search(pattern, actual) is not None

        case Ignore():
            return True

    return False


# === Test Case ===


@dataclass
class BashTest:
    """A test case for bash compatibility."""
    code: str
    name: str = ''
    category: str = ''  # Test category for grouping (e.g., 'echo', 'variable', 'heredoc')
    description: str = ''
    setup_env: dict[str, str] = field(default_factory=dict)
    skip: str | None = None
    check_stdout: bool = True  # Whether to compare stdout (False for exit-code-only tests)
    check_stderr: bool = True  # Whether to compare stderr
