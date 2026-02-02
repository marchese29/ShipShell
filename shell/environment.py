from __future__ import annotations

import os
import platform
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import ShellRunnable
    from .trap import TrapManager


class ColonDelimitedPath(list[Path]):
    """List of Paths that serializes to/from colon-separated string.

    Used for PATH-like environment variables. Constructor only accepts
    a colon-separated string for parsing (so type coercion works via type(value)).
    """

    def __init__(self, colon_string: str = ''):
        """Parse from colon-separated string."""
        if colon_string:
            paths = [Path(p) for p in colon_string.split(':') if p]
        else:
            paths = []
        super().__init__(paths)

    def __str__(self) -> str:
        return ':'.join(str(p) for p in self)

    def __repr__(self) -> str:
        return f'ColonDelimitedPath({list.__repr__(self)})'


def env_to_str(e: Any) -> str:
    """Convert an environment value to a string suitable for passing to execve."""
    if e is None:
        return ''
    return str(e)


class ShellEnvironment(MutableMapping):
    """The shell's environment"""

    def __init__(self):
        self._env: dict[str, Any] = {}
        self._exported: set[str] = set()
        self._type_hints: dict[str, type] = {}  # For bash sync-back type restoration

        # Special Variables
        self._dir_stack: list[Path] = [Path.home()]
        self._last_exit: int = 0

        # Variables with computed values
        self._home: Path = Path.home()
        self._old_pwd: Path | None = None
        self._pid: int = os.getpid()
        self._ppid: int = os.getppid()
        self._path: ColonDelimitedPath = ColonDelimitedPath()
        self._path_cache: dict[str, Path] = {}
        self._pwd: Path = Path.cwd()
        self._pysh_config_dir: Path = Path.home() / '.config' / 'pysh'
        self._shlvl: int = 0

        # Shell options
        self._physical: bool = False  # Don't follow symlinks in cd/pwd (-P)

        # Trap system
        self._traps: TrapManager | None = None
        self._current_runnable: ShellRunnable | None = None
        self._last_runnable: ShellRunnable | None = None

    @property
    def last_exit(self) -> int:
        """Get the last exit code ($?)."""
        return self._last_exit

    @last_exit.setter
    def last_exit(self, code: int):
        """Set the last exit code ($?)."""
        self._last_exit = code

    @property
    def physical(self) -> bool:
        """Get the physical mode setting (-P)."""
        return self._physical

    @physical.setter
    def physical(self, value: bool):
        """Set the physical mode setting (-P)."""
        self._physical = value

    @property
    def traps(self) -> TrapManager:
        """Get the trap manager (lazy initialized)."""
        if self._traps is None:
            # Avoid circular: environment.py ← trap.py ← model.py
            from .trap import TrapManager  # noqa: PLC0415

            self._traps = TrapManager()
        return self._traps

    @property
    def current_runnable(self) -> ShellRunnable | None:
        """The runnable currently executing. For DEBUG trap handlers."""
        return self._current_runnable

    @current_runnable.setter
    def current_runnable(self, value: ShellRunnable | None):
        self._current_runnable = value

    @property
    def last_runnable(self) -> ShellRunnable | None:
        """The last runnable that completed. For ERR/TRACE trap handlers."""
        return self._last_runnable

    @last_runnable.setter
    def last_runnable(self, value: ShellRunnable | None):
        self._last_runnable = value

    # Variables with computed values that can't be set via __setitem__
    # TODO: We should be able to inherit some of these from the parent environment
    # (e.g. HOME, PATH) rather than always computing them ourselves
    _COMPUTED_VARS = frozenset({'?', '$', 'HOME', 'OLDPWD', 'PATH', 'PPID', 'PWD', 'SHLVL'})

    def initialize(self) -> ShellEnvironment:
        # Inherit regular variables as STRINGS (no implicit type coercion)
        # Skip computed variables - they're handled specially with explicit typing
        for key in os.environ.keys():
            if key not in self._COMPUTED_VARS:
                self._env[key] = os.environ[key]  # Store as string
                self._exported.add(key)

        # Computed variables with explicit typed inheritance
        if home := os.environ.get('HOME'):
            self._home = Path(home)

        # Check for PYSH_CONFIG_DIR override from parent environment
        if pysh_config_dir := os.environ.get('PYSH_CONFIG_DIR'):
            self._pysh_config_dir = Path(pysh_config_dir)

        # Inherit PATH from parent environment, or use default
        if parent_path := os.environ.get('PATH'):
            self._path = ColonDelimitedPath(parent_path)
        else:
            # Fallback default PATH
            self._path = ColonDelimitedPath('/usr/bin:/bin')
            if platform.system() == 'Darwin':
                self._path.extend([Path('/usr/sbin'), Path('/sbin')])

        # SHLVL: parse from parent string, increment
        parent_shlvl = os.environ.get('SHLVL', '0')
        try:
            self._shlvl = int(parent_shlvl) + 1
        except ValueError:
            self._shlvl = 1

        # Sync computed variables to os.environ
        os.environ['HOME'] = str(self._home)
        os.environ['PATH'] = str(self._path)
        os.environ['PWD'] = str(self._pwd)
        os.environ['OLDPWD'] = str(self._old_pwd) if self._old_pwd else ''
        os.environ['PPID'] = str(self._ppid)
        os.environ['SHLVL'] = str(self._shlvl)
        os.environ['PYSH_CONFIG_DIR'] = str(self._pysh_config_dir)

        return self

    def exported(self) -> dict[str, Any]:
        return {k: self._env.get(k, None) for k in self._exported}

    def export(self, name: str):
        self._exported.add(name)

    def unexport(self, name: str):
        self._exported.remove(name)

    def chdir(self, directory: Path):
        self._old_pwd = self._pwd
        self._pwd = directory
        self._dir_stack[0] = directory
        # Sync to os.environ
        os.environ['PWD'] = str(directory)
        if self._old_pwd is not None:
            os.environ['OLDPWD'] = str(self._old_pwd)

    def pushd(self, directory: Path | None = None):
        if directory is None:
            # Swap when no args are provided
            if len(self._dir_stack) < 2:
                raise OSError('no other directory')
            self._dir_stack[0], self._dir_stack[1] = (
                self._dir_stack[1],
                self._dir_stack[0],
            )
            self.chdir(self._dir_stack[0])
            return
        self._dir_stack.insert(0, directory)
        self.chdir(directory)

    def pushd_rot(self, n: int):
        if n == 0:
            return

        if n < 0:
            n += len(self._dir_stack)

        if n >= len(self._dir_stack) or n < 0:
            raise OSError('directory stack index out of range')

        self._dir_stack = self._dir_stack[n:] + self._dir_stack[:n]
        self.chdir(self._dir_stack[0])

    def popd(self, n: int | None = None):
        if n is None or n == 0:
            if len(self._dir_stack) == 1:
                return
            self._dir_stack.pop(0)
            self.chdir(self._dir_stack[0])
            return

        if n < 0:
            n += len(self._dir_stack)

        if n >= len(self._dir_stack) or n < 0:
            raise OSError('directory stack index out of range')

        self._dir_stack.pop(n)

    @property
    def dir_stack(self) -> list[Path]:
        return self._dir_stack.copy()

    @property
    def home(self) -> Path | None:
        return self._home

    @property
    def old_pwd(self) -> Path | None:
        return self._old_pwd

    @old_pwd.setter
    def old_pwd(self, value: Path | None):
        self._old_pwd = value
        os.environ['OLDPWD'] = str(value) if value is not None else ''

    @property
    def path(self) -> ColonDelimitedPath:
        return self._path

    @path.setter
    def path(self, value: str | list[str] | list[Path] | ColonDelimitedPath):
        if isinstance(value, ColonDelimitedPath):
            self._path = value
        elif isinstance(value, str):
            self._path = ColonDelimitedPath(value)
        else:
            # list of strings or Paths
            self._path = ColonDelimitedPath()
            self._path.extend(Path(p) if isinstance(p, str) else p for p in value)
        os.environ['PATH'] = str(self._path)

    @property
    def pwd(self) -> Path:
        return self._pwd

    @pwd.setter
    def pwd(self, value: Path):
        self._pwd = value
        os.environ['PWD'] = str(value)

    @property
    def config_dir(self) -> Path:
        return self._pysh_config_dir

    @config_dir.setter
    def config_dir(self, value: Path):
        self._pysh_config_dir = value
        os.environ['PYSH_CONFIG_DIR'] = str(value)

    def __getitem__(self, key: str) -> Any | None:
        match key:
            case 'HOME':
                return self._home
            case 'OLDPWD':
                return self._old_pwd
            case 'PATH':
                return self._path
            case 'PPID':
                return self._ppid
            case 'PWD':
                return self._pwd
            case 'PYSH_CONFIG_DIR':
                return self._pysh_config_dir
            case 'SHLVL':
                return self._shlvl
            case '?':
                return self._last_exit
            case '$':
                return self._pid
            case _:
                return self._env[key]

    def get_type_hint(self, key: str) -> type | None:
        """Get the type hint for a variable (for bash sync-back)."""
        return self._type_hints.get(key)

    def clear_type_hint(self, key: str) -> None:
        """Clear the type hint for a variable."""
        self._type_hints.pop(key, None)

    def __setitem__(self, key: str, value: Any):
        match key:
            case '?' | '$' | 'HOME' | 'OLDPWD' | 'PATH' | 'PPID' | 'PWD' | 'SHLVL' as k:
                raise NotImplementedError(f'Direct manipulation of {k} in environment')
            case 'PYSH_CONFIG_DIR':
                self._pysh_config_dir = Path(value) if not isinstance(value, Path) else value
                os.environ[key] = str(self._pysh_config_dir)
            case _:
                self._env[key] = value
                # Record type hint for non-string values (for bash sync-back)
                if not isinstance(value, str):
                    self._type_hints[key] = type(value)
                elif key in self._type_hints:
                    del self._type_hints[key]
                os.environ[key] = env_to_str(value)

    def __delitem__(self, key: str):
        match key:
            case (
                '?'
                | '$'
                | 'HOME'
                | 'OLDPWD'
                | 'PATH'
                | 'PPID'
                | 'PWD'
                | 'PYSH_CONFIG_DIR'
                | 'SHLVL'
            ):
                raise ValueError('Cannot delete built-in environment variables')
            case _:
                del self._env[key]
                os.environ.pop(key, None)

    def __iter__(self) -> Iterator:
        return iter(self._env)

    def __contains__(self, key: object) -> bool:
        match key:
            case (
                '?'
                | '$'
                | 'HOME'
                | 'OLDPWD'
                | 'PATH'
                | 'PPID'
                | 'PWD'
                | 'PYSH_CONFIG_DIR'
                | 'SHLVL'
            ):
                return True
            case _:
                return key in self._env

    def __len__(self) -> int:
        # Extras: HOME, OLDPWD, PATH, PPID, PWD, PYSH_CONFIG_DIR, SHLVL
        return len(self._env) + 7


env: ShellEnvironment = ShellEnvironment()
