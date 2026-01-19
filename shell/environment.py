from __future__ import annotations

import os
import platform
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any


# I'm sure I will come to regret this function some day
def _str_to_env(s: str | None) -> Any | None:
    # 1. Empty string -> None
    if s is None or len(s) == 0:
        return None

    # 2. Exact "True"/"False" -> bool
    if s in ('True', 'False'):
        return bool(s)

    # 3. Valid number containing decimal -> float
    if '.' in s:
        try:
            return float(s)
        except ValueError:
            pass
    else:
        # 4. Valid integer -> int
        try:
            return int(s)
        except ValueError:
            pass

    # 5. String with colons -> list(recurse)
    if ':' in s:
        return [_str_to_env(i) for i in s.split(':')]

    # 6. Looks like a path -> Path
    if '/' in s or s.startswith('~'):
        return Path(s)

    # 7. It's a string
    return s


def env_to_str(e: Any) -> str:
    """
    Convert an environment value to a string suitable for passing to execve.
    Inverse of _str_to_env - handles lists by joining with colons.
    """
    if e is None:
        return ''

    # List -> join with colons (e.g., PATH)
    if isinstance(e, list):
        return ':'.join(env_to_str(item) for item in e)

    # Everything else converts via str()
    return str(e)


class ShellEnvironment(MutableMapping):
    """The shell's environment"""

    def __init__(self):
        self._env: dict[str, Any] = {}
        self._exported: set[str] = set()

        # Special Variables
        self._dir_stack: list[Path] = [Path.home()]
        self._last_exit: int = 0

        # Variables with computed values
        self._home: Path = Path.home()
        self._old_pwd: Path | None = None
        self._pid: int = os.getpid()
        self._ppid: int = os.getppid()
        self._path: list[Path] = []
        self._path_cache: dict[str, Path] = {}
        self._pwd: Path = Path.cwd()
        self._pysh_config_dir: Path = Path.home() / '.config' / 'pysh'
        self._shlvl: int = 0

    # Variables with computed values that can't be set via __setitem__
    # TODO: We should be able to inherit some of these from the parent environment
    # (e.g. HOME, PATH) rather than always computing them ourselves
    _COMPUTED_VARS = frozenset({'?', '$', 'HOME', 'OLDPWD', 'PATH', 'PPID', 'PWD', 'SHLVL'})

    def initialize(self) -> ShellEnvironment:
        # Inherit from the parent environment, inputs are exported
        # Skip computed variables - they're handled specially
        for key in os.environ.keys():
            if key not in self._COMPUTED_VARS:
                self[key] = _str_to_env(os.environ.get(key))
                self._exported.add(key)

        # Initialize computed variables from parent environment
        if home := os.environ.get('HOME'):
            self._home = Path(home)

        # Check for PYSH_CONFIG_DIR override from parent environment
        if pysh_config_dir := os.environ.get('PYSH_CONFIG_DIR'):
            self._pysh_config_dir = Path(pysh_config_dir)

        # Inherit PATH from parent environment, or use default
        if parent_path := os.environ.get('PATH'):
            self._path = [Path(p) for p in parent_path.split(':') if p]
        else:
            # Fallback default PATH
            paths = [Path('/usr/bin'), Path('/bin')]
            if platform.system() == 'Darwin':
                paths.extend([Path('/usr/sbin'), Path('/sbin')])
            self._path = paths

        if 'SHLVL' in self._env and isinstance(old_shlvl := self._env['SHLVL'], int):
            self._shlvl = old_shlvl + 1
            del self._env['SHLVL']

        # Sync computed variables to os.environ
        os.environ['HOME'] = str(self._home)
        os.environ['PATH'] = ':'.join(str(p) for p in self._path)
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
    def path(self) -> list[Path]:
        return self._path.copy()

    @path.setter
    def path(self, value: list[Path]):
        self._path = value
        os.environ['PATH'] = ':'.join(str(p) for p in value)

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

    def __setitem__(self, key: str, value: Any):
        match key:
            case '?' | '$' | 'HOME' | 'OLDPWD' | 'PATH' | 'PPID' | 'PWD' | 'SHLVL' as k:
                raise NotImplementedError(f'Direct manipulation of {k} in environment')
            case 'PYSH_CONFIG_DIR':
                self._pysh_config_dir = Path(value) if not isinstance(value, Path) else value
                os.environ[key] = str(self._pysh_config_dir)
            case _:
                self._env[key] = value
                os.environ[key] = env_to_str(value)

    def __delitem__(self, key: str):
        match key:
            case (
                '?' | '$' | 'HOME' | 'OLDPWD' | 'PATH' | 'PPID'
                | 'PWD' | 'PYSH_CONFIG_DIR' | 'SHLVL'
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
                '?' | '$' | 'HOME' | 'OLDPWD' | 'PATH' | 'PPID'
                | 'PWD' | 'PYSH_CONFIG_DIR' | 'SHLVL'
            ):
                return True
            case _:
                return key in self._env

    def __len__(self) -> int:
        # Extras: HOME, OLDPWD, PATH, PPID, PWD, PYSH_CONFIG_DIR, SHLVL
        return len(self._env) + 7


env: ShellEnvironment = ShellEnvironment()
