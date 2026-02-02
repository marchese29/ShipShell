"""Bash compatibility layer - executes bash code with state synchronization.

This module provides `source_bash()` which runs bash code in a real bash subprocess
while synchronizing state (environment variables, functions, pwd) back to Python.

State is transferred via environment variables and a dedicated pipe for the epilogue.

Usage:
    from shell.compat.bash import source_bash

    # Source bash code - modifies environment, wires functions
    source_bash('export FOO=bar')()
    source_bash('my_func() { echo "hi"; }')()

    # Composable with pipelines
    source_bash('ls -la') | grep('txt')

    # Functions are composable too
    source_bash('greet() { echo "hi $1"; }')()
    greet('world') | prog('cat')  # Returns a runnable

    # Skip function wiring with scope=None
    source_bash('helper() { ... }', scope=None)()
"""

from __future__ import annotations

import keyword
import os
import shlex
import types
import warnings
from collections.abc import Callable

from shell.builtins import cd
from shell.environment import env, env_to_str
from shell.model import Command, IOConfig, ShellResult, ShellRunnable

# Prefer homebrew bash (5.x) over macOS /bin/bash (3.2)
# Must both exist AND be executable
_HOMEBREW_BASH = '/opt/homebrew/bin/bash'
if os.path.exists(_HOMEBREW_BASH) and os.access(_HOMEBREW_BASH, os.X_OK):
    BASH_PATH = _HOMEBREW_BASH
else:
    BASH_PATH = '/bin/bash'

# macOS requires high FD numbers for /dev/fd/N to work reliably after fork+exec.
# Bash uses FDs starting at 63 for process substitution. We use 62 for state pipe.
_STATE_FD_BASE = 62

# Variables that shouldn't be synced (read-only or managed internally)
_SKIP_VARS = frozenset(
    {
        '?',  # Exit code - managed by shell
        '$',  # PID - read-only
        'PPID',  # Parent PID - read-only
        'SHLVL',  # Shell level - managed
        'PWD',  # Handled separately
        'OLDPWD',  # Handled by cd
        '_',  # Last argument - bash internal
        'BASH_VERSION',  # Bash-specific
        'BASH_VERSINFO',  # Bash-specific
        'BASH',  # Bash path
        'BASHOPTS',  # Bash options
        'SHELLOPTS',  # Shell options
        'MACHTYPE',  # Machine type
        'OSTYPE',  # OS type
        'HOSTTYPE',  # Host type
        'HOSTNAME',  # Hostname
        'RANDOM',  # Random number
        'LINENO',  # Line number
        'SECONDS',  # Seconds since shell start
        'BASH_COMMAND',  # Current command
        'BASH_SUBSHELL',  # Subshell level
        'FUNCNAME',  # Function name stack
        'BASH_LINENO',  # Line number stack
        'BASH_SOURCE',  # Source file stack
        'COLUMNS',  # Terminal columns
        'LINES',  # Terminal lines
        'TERM',  # Terminal type
    }
)


def _generate_preamble(args: list[str] | None = None) -> str:
    """Generate minimal preamble for bash execution.

    Just cd to the correct directory - variables come via environment.
    """
    preamble = f'cd {shlex.quote(str(env.pwd))}'
    if args:
        preamble += f'\nset -- {" ".join(shlex.quote(a) for a in args)}'
    return preamble


def _build_env_overlay() -> tuple[dict[str, str], set[str]]:
    """Build environment overlay for Command, return overlay and sent keys.

    The sent_keys are used for unexport tracking - if a key we sent doesn't
    come back in the epilogue, it was unset or unexported in bash.
    """
    overlay: dict[str, str] = {}
    sent_keys: set[str] = set()

    # Get all variables from environment
    for name, value in env.items():
        if name in _SKIP_VARS:
            continue
        overlay[name] = env_to_str(value)
        sent_keys.add(name)

    return overlay, sent_keys


# Epilogue template - reads state from bash and writes to a dedicated fd.
# The {fd} placeholder is filled in at runtime to avoid conflicts with IOConfig.
_EPILOGUE_TEMPLATE = """
__exit=$?
{{
    # Export all functions so they appear in env -0 output
    for __func in $(declare -F | cut -d" " -f3); do
        export -f "$__func" 2>/dev/null || true
    done
    unset __func

    echo '<<<ENV>>>'
    env -0  # Null-separated key=value pairs
    printf '\\0\\n'  # Terminate last entry AND add newline for section parsing
    echo '<<<PWD>>>'
    pwd
    echo '<<<END>>>'
}} >&{fd}
exit $__exit
"""


def _parse_epilogue(output: str) -> tuple[dict[str, str], str]:
    """Parse epilogue output into (env_dict, pwd).

    The epilogue format is:
        <<<ENV>>>
        KEY=value\0KEY2=value2\0...
        <<<PWD>>>
        /current/directory
        <<<END>>>
    """
    sections = _split_sections(output)

    # Parse env -0 output (null-separated)
    env_dict: dict[str, str] = {}
    if 'ENV' in sections:
        for entry in sections['ENV'].split('\0'):
            entry = entry.strip()
            if '=' in entry:
                key, value = entry.split('=', 1)
                env_dict[key] = value

    pwd = sections.get('PWD', '').strip()

    return env_dict, pwd


def _split_sections(output: str) -> dict[str, str]:
    """Split epilogue output by <<<SECTION>>> markers."""
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []

    for line in output.split('\n'):
        if line.startswith('<<<') and line.endswith('>>>'):
            if current:
                sections[current] = '\n'.join(lines)
            current = line[3:-3]
            lines = []
        else:
            lines.append(line)

    return sections


def _update_state(
    child_env: dict[str, str],
    sent_keys: set[str],
    pwd: str,
) -> None:
    """Apply captured state back to ShellEnvironment.

    Args:
        child_env: Environment dict from env -0 output
        sent_keys: Keys we sent to bash in the overlay
        pwd: Working directory from bash
    """
    # 1. Update variables from child environment (including BASH_FUNC_* for persistence)
    for name, str_value in child_env.items():
        if name in _SKIP_VARS:
            continue

        # Try to restore original type using type registry
        original_type = env.get_type_hint(name)
        if original_type is not None:
            try:
                # type(string_value) works for int, Path, ColonDelimitedPath, etc.
                value = original_type(str_value)
            except (ValueError, TypeError):
                # Coercion failed, keep as string and clear type hint
                value = str_value
                env.clear_type_hint(name)
        else:
            value = str_value

        # Update both regular vars and BASH_FUNC_* entries
        # BASH_FUNC_* entries are stored in os.environ so they're inherited by future calls
        env[name] = value

    # 2. Handle unexports: keys we sent that are no longer in child env
    child_keys = {k for k in child_env if not k.startswith('BASH_FUNC_')}
    for name in sent_keys - child_keys:
        if name in _SKIP_VARS:
            continue
        # Variable was unexported or unset in bash
        try:
            env.unexport(name)
            env.clear_type_hint(name)
        except KeyError:
            pass  # Already unexported

    # 3. Update working directory using cd builtin
    if pwd and pwd != str(env.pwd):
        cd(pwd)()


def _bash_to_python_name(bash_name: str) -> str:
    """Convert bash function name to valid Python identifier.

    Examples:
        'my-func' -> 'my_func'     # Dashes to underscores
        '123func' -> '_123func'    # Prefix underscore for leading digit
        'exit'    -> 'exit_'       # Append underscore for keywords
    """
    py_name = bash_name.replace('-', '_')
    if py_name and py_name[0].isdigit():
        py_name = '_' + py_name
    if keyword.iskeyword(py_name):
        py_name = py_name + '_'
    return py_name


def _make_bash_function_wrapper(bash_name: str) -> Callable[..., BashSource]:
    """Create Python callable that returns a BashSource for the function call.

    The wrapper returns a ShellRunnable, allowing composition:
        my_func('arg') | grep('pattern')
        my_func('arg')()  # Execute immediately
    """

    def wrapper(*args: str) -> BashSource:
        args_str = ' '.join(shlex.quote(str(a)) for a in args)
        cmd = f'{shlex.quote(bash_name)} {args_str}' if args else shlex.quote(bash_name)
        return source_bash(cmd)

    wrapper.__name__ = bash_name
    wrapper.__doc__ = f'Bash function: {bash_name}. Returns a ShellRunnable for composition.'
    return wrapper


def _wire_bash_functions(child_env: dict[str, str], scope: str) -> None:
    """Wire bash functions from env into a Python namespace.

    Args:
        child_env: Environment dict containing BASH_FUNC_* entries
        scope: '__main__' for root namespace, or module name like 'f' for f.foo()
    """
    import __main__  # noqa: PLC0415 - must be imported at call time, not module load

    # Get or create target namespace
    if scope == '__main__':
        target_ns = __main__.__dict__
    else:
        if hasattr(__main__, scope):
            module = getattr(__main__, scope)
        else:
            module = types.ModuleType(scope)
            setattr(__main__, scope, module)
        target_ns = module.__dict__

    for key, _value in child_env.items():
        if key.startswith('BASH_FUNC_') and key.endswith('%%'):
            bash_name = key[10:-2]  # Strip 'BASH_FUNC_' prefix and '%%' suffix
            py_name = _bash_to_python_name(bash_name)

            # Warn if the name was changed for Python compatibility
            if py_name != bash_name:
                warnings.warn(
                    f"Bash function '{bash_name}' renamed to '{py_name}' for Python compatibility",
                    UserWarning,
                    stacklevel=2,
                )

            target_ns[py_name] = _make_bash_function_wrapper(bash_name)


def _find_state_fd(io: IOConfig | None) -> int:
    """Find a high fd that doesn't conflict with IOConfig redirections.

    Uses high FD numbers (62+) for macOS /dev/fd/N compatibility.
    """
    used_fds = {0, 1, 2}  # stdin/stdout/stderr always in use
    if io and io.extra_fds:
        used_fds.update(io.extra_fds.keys())

    # Start at high fd (62) for macOS compatibility, find first unused
    fd = _STATE_FD_BASE
    while fd in used_fds:
        fd += 1
    return fd


class BashSource(ShellRunnable):
    """A runnable that sources bash code with state synchronization.

    stdout/stderr pass through to terminal. State is transferred via a
    dedicated pipe fd, parsed after execution to sync back to Python.
    Always uses the global shell environment.
    """

    def __init__(
        self,
        code: str,
        args: list[str] | None = None,
        scope: str | None = '__main__',
    ):
        super().__init__()
        self._code = code
        self._args = args
        self._scope = scope

    def _exec(self, io: IOConfig | None = None) -> ShellResult:
        """Execute bash with state sync, passing stdout/stderr through."""
        # Merge IO configs (self._io takes precedence over passed io)
        merged_io = self._io.merge_over(io)

        # Build preamble with args
        preamble = _generate_preamble(self._args)

        # Find state fd that doesn't conflict (uses high fd for macOS)
        state_fd = _find_state_fd(merged_io)

        # Generate epilogue script with dynamic fd
        epilogue = _EPILOGUE_TEMPLATE.format(fd=state_fd)
        full_script = f'{preamble}\n{self._code}\n{epilogue}'

        # Create pipe for state transfer
        state_r, state_w = os.pipe()

        # Build Command with scalar overlay
        overlay, sent_keys = _build_env_overlay()
        cmd = Command(BASH_PATH, '-c', full_script).env(**overlay)

        pid = os.fork()
        if pid == 0:
            # Child: close read end, move write end to high state_fd
            os.close(state_r)
            os.dup2(state_w, state_fd)
            os.close(state_w)

            # Execute bash with merged IO (stdout/stderr pass through or redirect)
            result = cmd._exec(merged_io)
            os._exit(result.exit_code)
        else:
            # Parent: close write end, wait for child, read state
            os.close(state_w)
            _, status = os.waitpid(pid, 0)
            exit_code = os.waitstatus_to_exitcode(status)

            # Read state from pipe
            with os.fdopen(state_r, 'r') as f:
                state_output = f.read()

            # Parse epilogue and update state
            child_env, pwd = _parse_epilogue(state_output)
            _update_state(child_env, sent_keys, pwd)

            # Wire functions unless scope is None
            if self._scope is not None:
                _wire_bash_functions(child_env, self._scope)

            env.last_exit = exit_code
            return ShellResult(exit_code)


def source_bash(
    code: str,
    args: list[str] | None = None,
    scope: str | None = '__main__',
) -> BashSource:
    """Create a BashSource runnable for sourcing bash code.

    Like bash's `source` command - modifies the global environment and wires functions.
    For isolated bash execution without state sync, use prog('bash')('-c', code).

    Args:
        code: Bash code to execute
        args: Optional positional arguments (accessible as $1, $2, etc.)
        scope: Namespace to wire functions into. Use '__main__' for root namespace,
               a module name like 'f' for f.foo(), or None to skip function wiring.

    Returns:
        A ShellRunnable that can be composed with pipes, conditionals, etc.

    Examples:
        source_bash('echo hi')()                    # Execute immediately
        source_bash('echo hi') > 'out.txt'          # Redirect stdout
        source_bash('echo hi') | grep('h')          # Pipeline (output visible)
        source_bash('greet() { echo hi; }')()       # Define function
        greet('world')()                            # Call and execute
        greet('world') | prog('cat')                # Compose with other runnables
        source_bash('helper() { ... }', scope=None)()  # Skip function wiring
    """
    return BashSource(code, args, scope)
