import inspect
import os
import sys
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from pydantic import BeforeValidator, ConfigDict, ValidationError, validate_call

from .environment import env
from .model import BUILTIN_REGISTRY, InProcessCallable
from .trap import TrapType
from .types import ShellError, ShellInt, ShellPath


class _Flag:
    """Annotate a parameter with short flag names (without dash).

    Boolean params: presence of flag sets to True.
        physical: Annotated[bool, _Flag("P")] = False
        Calling with -P sets physical=True.

    Non-boolean params: flag consumes next value.
        count: Annotated[int, _Flag("n")] = 1
        Calling with -n 5 sets count=5.
    """

    def __init__(self, *short: str):
        self.shorts: list[str] = list(short)


def _is_bool_type(hint) -> bool:
    """Check if a type hint is a boolean type."""
    origin = get_origin(hint)
    if origin is Annotated:
        hint = get_args(hint)[0]
    return hint is bool


def _is_variadic_tuple(hint) -> bool:
    """Check if a type hint is a variadic tuple like tuple[str, ...]."""
    origin = get_origin(hint)
    if origin is Annotated:
        hint = get_args(hint)[0]
        origin = get_origin(hint)
    if origin is tuple:
        args = get_args(hint)
        # tuple[T, ...] has args like (str, Ellipsis)
        return len(args) == 2 and args[1] is ...
    return False




def builtin_command[**P](f: Callable[P, None]) -> Callable[..., InProcessCallable]:
    """Decorator that wraps a function to behave as a shell builtin.

    Returns a Builtin runnable that can be composed with other shell operations
    (pipes, redirections) and executed via run().

    Provides two calling conventions:
    - Bash-style: cd("-P", "/tmp") - positional args with flags
    - Keyword-style: cd(target="/tmp", physical=True) - named parameters

    Flags are defined using Annotated[type, _Flag("X")] where "X" is the short
    flag name (without dash). Boolean flags set the param to True when present;
    non-boolean flags consume the next argument as their value.

    Exit codes follow bash convention:
    - 0: Success
    - 1: General errors (e.g., file not found)
    - 2: Misuse of builtin (e.g., invalid flag, wrong argument type)
    """
    validated: Callable[..., None] = validate_call(
        config=ConfigDict(validate_default=True)
    )(f)

    hints = get_type_hints(f, include_extras=True)
    sig = inspect.signature(f)
    param_names = list(sig.parameters.keys())

    # Map short flag names to (parameter_name, is_boolean)
    flag_mapping: dict[str, tuple[str, bool]] = {}
    # Track which parameters are flags (vs positional)
    flag_params: set[str] = set()

    for name, hint in hints.items():
        if get_origin(hint) is Annotated:
            for metadata in get_args(hint)[1:]:
                if isinstance(metadata, _Flag):
                    flag_params.add(name)
                    is_bool = _is_bool_type(hint)
                    for short_name in metadata.shorts:
                        flag_mapping[short_name] = (name, is_bool)

    # Parameters that accept positional args (non-flag params, in order)
    positional_params = [p for p in param_names if p not in flag_params]

    # Check if the last positional parameter is variadic (tuple[T, ...])
    variadic_param: str | None = None
    if positional_params:
        last_param = positional_params[-1]
        if last_param in hints and _is_variadic_tuple(hints[last_param]):
            variadic_param = last_param

    def impl(**kwargs) -> int:
        """Execute the builtin with the given kwargs, return exit code."""
        try:
            validated(**kwargs)
            return 0
        except ShellError as se:
            if se.message:
                print(f'{f.__name__}: {se.message}', file=sys.stderr)
            return se.exit_code
        except (TypeError, ValidationError) as e:
            print(f'{f.__name__}: {e}', file=sys.stderr)
            return 2
        except OSError as e:
            print(f'{f.__name__}: {e}', file=sys.stderr)
            return 1
        except Exception as e:
            print(
                f"{f.__name__}: unexpected exception '{type(e)}: {e}'", file=sys.stderr
            )
            return 1

    @wraps(f)
    def factory(*args, **kwargs) -> InProcessCallable:
        """Parse arguments and return an InProcessCallable runnable."""
        if args and kwargs:
            raise ShellError(
                'cannot mix positional and keyword arguments', exit_code=2
            )

        # Keyword style: pass through directly
        if kwargs:
            return InProcessCallable(lambda kw=kwargs: impl(**kw), name=f.__name__)

        # Bash style: parse args into kwargs
        parsed_kwargs: dict[str, Any] = {}
        positional_args: list[Any] = []
        end_of_flags = False  # Set to True after seeing '--'

        i = 0
        args_list = list(args)
        while i < len(args_list):
            arg = args_list[i]
            # Check for end-of-flags marker or if arg looks like a flag
            # Skip flag parsing if: end_of_flags is set, arg is '--', or arg is numeric (like -5)
            if not end_of_flags and isinstance(arg, str) and arg == '--':
                end_of_flags = True
                i += 1
                continue
            # Check if this looks like a flag (starts with - and isn't a number like -5)
            is_flag = False
            if (
                not end_of_flags
                and isinstance(arg, str)
                and arg.startswith('-')
                and len(arg) > 1
            ):
                # Not a flag if it looks like a negative number (e.g., -5, -3.14)
                after_dash = arg[1:].lstrip('-').replace('.', '', 1)
                is_flag = not (after_dash.isdigit() if after_dash else False)
            if is_flag:
                flags = arg[1:]
                for j, flag_char in enumerate(flags):
                    if flag_char not in flag_mapping:
                        raise ShellError(f'unknown flag: -{flag_char}', exit_code=2)

                    param_name, is_bool = flag_mapping[flag_char]
                    if is_bool:
                        parsed_kwargs[param_name] = True
                    else:
                        # Non-boolean flag consumes next value
                        if j + 1 < len(flags):
                            # Remaining chars in this flag group are the value
                            parsed_kwargs[param_name] = flags[j + 1:]
                            break
                        elif i + 1 < len(args_list):
                            i += 1
                            parsed_kwargs[param_name] = args_list[i]
                        else:
                            raise ShellError(
                                f'flag -{flag_char} requires a value', exit_code=2
                            )
            else:
                positional_args.append(arg)
            i += 1

        # Handle variadic parameter (collects remaining positional args)
        if variadic_param is not None:
            # All non-variadic positional params come before the variadic one
            non_variadic_params = positional_params[:-1]
            if len(positional_args) < len(non_variadic_params):
                # Not enough args for non-variadic params - let validation handle it
                for i, value in enumerate(positional_args):
                    parsed_kwargs[non_variadic_params[i]] = value
            else:
                # Assign non-variadic params first
                for i, name in enumerate(non_variadic_params):
                    parsed_kwargs[name] = positional_args[i]
                # Collect remaining into variadic param as tuple
                parsed_kwargs[variadic_param] = tuple(
                    positional_args[len(non_variadic_params):]
                )
        else:
            # No variadic parameter - strict positional count
            if len(positional_args) > len(positional_params):
                raise ShellError(
                    f'expected at most {len(positional_params)} positional '
                    f'argument(s), got {len(positional_args)}',
                    exit_code=2,
                )

            for i, value in enumerate(positional_args):
                parsed_kwargs[positional_params[i]] = value

        return InProcessCallable(
            lambda kw=parsed_kwargs: impl(**kw), name=builtin_name
        )

    # Register this builtin
    # Strip trailing underscore (used to avoid Python keyword conflicts like exit_)
    builtin_name = f.__name__.rstrip('_')
    BUILTIN_REGISTRY[builtin_name] = factory

    return factory


def _cd_args(arg: Any) -> Path:
    if arg is None:
        if env.home:
            return env.home
        else:
            raise ShellError('HOME not set')
    if isinstance(arg, str) and arg == '-':
        return env.old_pwd if env.old_pwd else Path.cwd()
    if isinstance(arg, Path):
        result = arg
    elif isinstance(arg, str):
        result = Path(arg)
    else:
        raise ShellError(f'invalid argument: {arg!r}', exit_code=2)

    if not result.exists():
        raise ShellError(f'no such directory: {result}')
    if not result.is_dir():
        raise ShellError(f'not a directory: {result}')
    return result


CdTarget = Annotated[Literal['-'] | str | Path | None, BeforeValidator(_cd_args)]


@overload
def cd(*args: Any) -> InProcessCallable: ...
@overload
def cd(*, target: CdTarget = ..., physical: bool = ...) -> InProcessCallable: ...
@builtin_command
def cd(target: CdTarget = None, physical: Annotated[bool, _Flag('P')] = False):
    """Change the current working directory.

    Args:
        target: Directory to change to. If None, changes to $HOME.
                Use "-" to change to the previous directory ($OLDPWD).
        physical: If True (-P flag), resolve symlinks in the path.
                  Also enabled by env.physical shell setting.

    Examples:
        cd("/tmp")              # Change to /tmp
        cd("-P", "/tmp")        # Change to /tmp, resolving symlinks
        cd("-")                 # Change to previous directory
        cd()                    # Change to $HOME
        cd(target="/tmp", physical=True)  # Keyword style
    """
    target = cast(Path, target)
    if physical or env.physical:
        target = target.resolve()
    env.chdir(target)
    os.chdir(target)


@overload
def pwd(*args: Any) -> InProcessCallable: ...
@overload
def pwd(*, physical: bool = ...) -> InProcessCallable: ...
@builtin_command
def pwd(physical: Annotated[bool, _Flag('P')] = False):
    """Print the current working directory.

    Args:
        physical: If True (-P flag), resolve symlinks to show the physical path.
                  Also enabled by env.physical shell setting.

    Examples:
        pwd()           # Print current directory
        pwd("-P")       # Print physical path (symlinks resolved)
    """
    if physical or env.physical:
        # Physical mode: show resolved path (no symlinks)
        path = Path.cwd().resolve()
    else:
        # Logical mode: show path as given to cd (preserves symlinks)
        path = env.pwd
    print(str(path))


@overload
def pushd(*args: Any) -> InProcessCallable: ...
@overload
def pushd(*, target: ShellInt | ShellPath | None = ...) -> InProcessCallable: ...
@builtin_command
def pushd(target: ShellInt | ShellPath | None = None):
    """Push a directory onto the stack and change to it.

    Args:
        target: Directory path to push, stack index to rotate to, or None.
                - Path: Push directory onto stack and cd to it.
                - Integer (+N/-N): Rotate stack so Nth entry is on top, then cd.
                - None: Swap the top two directories.

    Prints the directory stack after the operation.

    Examples:
        pushd("/tmp")   # Push /tmp and cd to it
        pushd()         # Swap top two directories
        pushd("+1")     # Rotate stack by 1 position
    """
    if isinstance(target, int):
        env.pushd_rot(target)
        os.chdir(env.dir_stack[0])
    elif isinstance(target, (str, Path)):
        path = Path(target) if isinstance(target, str) else target
        if not path.exists():
            raise ShellError(f'no such directory: {path}')
        if not path.is_dir():
            raise ShellError(f'not a directory: {path}')
        env.pushd(path)
        os.chdir(path)
    elif target is None:
        env.pushd(None)
        os.chdir(env.dir_stack[0])
    else:
        raise ShellError(f'invalid argument: {target!r}', exit_code=2)
    print(' '.join(str(d) for d in env.dir_stack))


@overload
def popd(*args: Any) -> InProcessCallable: ...
@overload
def popd(*, index: ShellInt | None = ...) -> InProcessCallable: ...
@builtin_command
def popd(index: ShellInt | None = None):
    """Pop a directory from the stack.

    Args:
        index: Stack index to remove, or None.
               - None or 0: Pop top directory and cd to the new top.
               - +N/-N: Remove the Nth entry without changing directory.

    Prints the directory stack after the operation.

    Examples:
        popd()      # Pop top and cd to new top
        popd("+1")  # Remove second entry, stay in current directory
    """
    index = cast(int | None, index)
    env.popd(index)
    if index is None or index == 0:
        os.chdir(env.dir_stack[0])
    print(' '.join(str(d) for d in env.dir_stack))


@overload
def dirs(*args: Any) -> InProcessCallable: ...
@overload
def dirs(
    *,
    clear: bool = ...,
    long_format: bool = ...,
    per_line: bool = ...,
) -> InProcessCallable: ...
@builtin_command
def dirs(
    clear: Annotated[bool, _Flag('c')] = False,
    long_format: Annotated[bool, _Flag('l')] = False,
    per_line: Annotated[bool, _Flag('p', 'v')] = False,
):
    """Display or clear the directory stack.

    Args:
        clear: If True (-c flag), clear the stack (keep only cwd).
        long_format: If True (-l flag), show full paths with symlinks resolved.
        per_line: If True (-p or -v flag), print one directory per line.

    Examples:
        dirs()          # Print stack space-separated
        dirs("-l")      # Print with symlinks resolved
        dirs("-p")      # Print one per line
        dirs("-c")      # Clear the stack
        dirs("-lp")     # Combine flags
    """
    if clear:
        env._dir_stack.clear()
        env._dir_stack.append(Path.cwd())
        return

    stack = env.dir_stack
    if long_format:
        stack = [d.resolve() for d in stack]

    if per_line:
        for directory in stack:
            print(directory)
    else:
        print(' '.join(str(d) for d in stack))


@overload
def exit_(*args: Any) -> InProcessCallable: ...
@overload
def exit_(*, code: int = ...) -> InProcessCallable: ...
@builtin_command
def exit_(code: int = 0):
    """Exit the shell with the given exit code.

    Uses sys.exit() rather than os._exit() so the exit can be caught and
    EXIT traps can fire before the process terminates.

    Args:
        code: Exit code to return to the parent process. Defaults to 0.

    Examples:
        exit_()     # Exit with code 0
        exit_(1)    # Exit with code 1
    """
    sys.exit(code)


def _find_in_path(name: str, find_all: bool = False) -> list[Path]:
    """Find a program in PATH.

    Args:
        name: The program name to search for.
        find_all: If True, returns all instances; if False, returns only the first.

    Returns:
        List of Path objects for matching executables (empty if not found).
    """
    results: list[Path] = []
    env_path: list[Path] = env.get('PATH', [])

    for path_dir in env_path:
        candidate = path_dir / name
        if candidate.exists() and candidate.is_file() and os.access(candidate, os.X_OK):
            results.append(candidate)
            if not find_all:
                break

    return results


@builtin_command
def which(
    commands: tuple[str, ...] = (),
    all_instances: Annotated[bool, _Flag('a')] = False,
    silent: Annotated[bool, _Flag('s')] = False,
):
    """Locate commands by name.

    Checks builtins first, then searches PATH for executables.

    Args:
        commands: One or more command names to look up.
        all_instances: If True (-a flag), show all instances in PATH,
                       not just the first. Also searches PATH for builtins.
        silent: If True (-s flag), suppress output and only set exit code.

    Exit codes:
        0: All commands found
        1: One or more commands not found (or no commands specified)

    Examples:
        which("ls")              # Print path to ls
        which("ls", "cat")       # Look up multiple commands
        which("-a", "python")    # Show all python instances in PATH
        which("-s", "cd")        # Silent check if cd exists
    """
    if not commands:
        raise ShellError('missing argument', exit_code=1)

    all_found = True

    for command in commands:
        found_anything = False

        # Check builtins first
        is_builtin = command in BUILTIN_REGISTRY
        if is_builtin:
            if not silent:
                print(f'{command}: shell built-in command')
            found_anything = True

            # If not showing all, skip searching PATH
            if not all_instances:
                continue

        # Search in PATH (either not a built-in, or all_instances is requested)
        paths = _find_in_path(command, find_all=all_instances)

        if paths:
            found_anything = True
            if not silent:
                for path in paths:
                    print(str(path))

        if not found_anything:
            all_found = False
            if not silent:
                print(f'{command} not found', file=sys.stderr)

    if not all_found:
        raise ShellError('', exit_code=1)


def _source_file_arg(arg: Any) -> Path:
    """Validate and resolve a source file path."""
    if arg is None:
        raise ShellError('missing file argument', exit_code=1)
    if isinstance(arg, Path):
        result = arg
    elif isinstance(arg, str):
        result = Path(arg).expanduser().resolve()
    else:
        raise ShellError(f'invalid file argument: {arg!r}', exit_code=2)

    if not result.exists():
        raise ShellError(f'no such file: {result}')
    if not result.is_file():
        raise ShellError(f'not a file: {result}')
    return result


SourceFile = Annotated[str | Path, BeforeValidator(_source_file_arg)]


@builtin_command
def source(file: SourceFile, args: tuple[str, ...] = ()):
    """
    Execute a Python file in the shell's namespace.

    The script runs in the current shell namespace, so definitions persist.
    Optional arguments are passed as sys.argv[1:].

    To run without modifying the current namespace, wrap in sub():
        sub(source("script.py", "arg1"))()

    Args:
        file: Path to the Python file (supports ~ expansion).
        args: Optional arguments passed to the script as sys.argv[1:].

    Examples:
        source("~/.pyshrc")()           # Load config file
        source("script.py", "arg1")()   # Run with sys.argv = ["script.py", "arg1"]
        sub(source("tool.py"))()        # Run isolated (like ./tool.py)
    """
    file = cast(Path, file)

    import __main__

    # Save and set sys.argv and __file__
    saved_argv = sys.argv
    saved_file = __main__.__dict__.get('__file__')
    sys.argv = [str(file)] + list(args)
    __main__.__dict__['__file__'] = str(file)

    try:
        code = file.read_text()
        exec(compile(code, str(file), 'exec'), __main__.__dict__)
    finally:
        sys.argv = saved_argv
        if saved_file is None:
            __main__.__dict__.pop('__file__', None)
        else:
            __main__.__dict__['__file__'] = saved_file


def _interpret_escapes(s: str) -> str:
    """Interpret bash-style escape sequences.

    Supports: \\\\ \\a \\b \\e \\E \\f \\n \\r \\t \\v \\0nnn \\xHH
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            c = s[i + 1]
            if c == '\\':
                result.append('\\')
                i += 2
            elif c == 'a':
                result.append('\a')
                i += 2
            elif c == 'b':
                result.append('\b')
                i += 2
            elif c in ('e', 'E'):
                result.append('\x1b')
                i += 2
            elif c == 'f':
                result.append('\f')
                i += 2
            elif c == 'n':
                result.append('\n')
                i += 2
            elif c == 'r':
                result.append('\r')
                i += 2
            elif c == 't':
                result.append('\t')
                i += 2
            elif c == 'v':
                result.append('\v')
                i += 2
            elif c == '0':
                # Octal: \0nnn (up to 3 octal digits)
                octal = ''
                j = i + 2
                while j < len(s) and len(octal) < 3 and s[j] in '01234567':
                    octal += s[j]
                    j += 1
                if octal:
                    result.append(chr(int(octal, 8)))
                else:
                    result.append('\0')
                i = j
            elif c == 'x':
                # Hex: \xHH (1-2 hex digits)
                hex_chars = ''
                j = i + 2
                while j < len(s) and len(hex_chars) < 2 and s[j] in '0123456789abcdefABCDEF':
                    hex_chars += s[j]
                    j += 1
                if hex_chars:
                    result.append(chr(int(hex_chars, 16)))
                    i = j
                else:
                    result.append(s[i])
                    i += 1
            else:
                # Unknown escape - keep as-is
                result.append(s[i])
                i += 1
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


@builtin_command
def echo(
    args: tuple[str, ...] = (),
    escape: Annotated[bool, _Flag('e')] = False,
    no_newline: Annotated[bool, _Flag('n')] = False,
):
    """Print arguments to stdout.

    Args:
        args: Strings to print, separated by spaces.
        escape: If True (-e flag), interpret backslash escape sequences.
        no_newline: If True (-n flag), do not output trailing newline.

    Escape sequences (with -e):
        \\\\  backslash
        \\a  alert (bell)
        \\b  backspace
        \\e  escape character
        \\f  form feed
        \\n  newline
        \\r  carriage return
        \\t  horizontal tab
        \\v  vertical tab
        \\0nnn  octal value (up to 3 digits)
        \\xHH  hexadecimal value (up to 2 digits)

    Examples:
        echo("hello", "world")      # Print "hello world"
        echo("-n", "no newline")    # Print without trailing newline
        echo("-e", "line1\\nline2") # Interpret escape sequences
    """
    output = ' '.join(args)
    if escape:
        output = _interpret_escapes(output)
    print(output, end='' if no_newline else '\n')


@builtin_command
def source_bash_code(code: str):
    """Execute bash code in the current shell environment.

    Runs the provided bash code string using the bash interpreter.
    Variables and state changes persist in the shell environment.

    Args:
        code: Bash code string to execute.

    Examples:
        source_bash_code("echo hello")()
        source_bash_code("for i in 1 2 3; do echo $i; done")()
        capture(source_bash_code("seq 1 10")).read_stdout()
    """
    from shell.compat import run_bash_code
    from shell.compat.bash import BashScriptError

    try:
        run_bash_code(code, env=env)
    except BashScriptError as e:
        raise ShellError(str(e), exit_code=e.exit_code) from e


def _bash_file_arg(arg: Any) -> Path:
    """Validate and resolve a bash script file path."""
    if arg is None:
        raise ShellError('missing file argument', exit_code=1)
    if isinstance(arg, Path):
        result = arg
    elif isinstance(arg, str):
        result = Path(arg).expanduser().resolve()
    else:
        raise ShellError(f'invalid file argument: {arg!r}', exit_code=2)

    if not result.exists():
        raise ShellError(f'no such file: {result}')
    if not result.is_file():
        raise ShellError(f'not a file: {result}')
    return result


BashFile = Annotated[str | Path, BeforeValidator(_bash_file_arg)]


@builtin_command
def source_bash_file(file: BashFile, args: tuple[str, ...] = ()):
    """Execute a bash script file in the current shell environment.

    Reads and executes the bash script. Positional arguments are passed
    to the script as $1, $2, etc.

    Args:
        file: Path to the bash script file (supports ~ expansion).
        args: Optional arguments passed to the script as positional parameters.

    Examples:
        source_bash_file("script.sh")()
        source_bash_file("setup.sh", "arg1", "arg2")()
        capture(source_bash_file("generate.sh")).read_stdout()
    """
    from shell.compat.bash import BashScriptError, run_bash_code

    file = cast(Path, file)
    code = file.read_text()

    try:
        run_bash_code(code, args=list(args), script_name=str(file), env=env)
    except BashScriptError as e:
        raise ShellError(str(e), exit_code=e.exit_code) from e


@builtin_command
def trap_list():
    """List available trap/signal names.

    Prints all trap types that can be used with env.traps.set().

    Examples:
        trap_list()()  # Print available trap names
    """
    for trap_type in TrapType:
        print(trap_type.name)


@builtin_command
def trap_show():
    """Show current trap settings.

    Displays all currently configured trap handlers in a format similar
    to bash's 'trap -p' output.

    Examples:
        trap_show()()  # Print current trap settings
    """
    output = env.traps.list()
    if output:
        print(output)
