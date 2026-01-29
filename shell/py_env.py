"""
User Python environment management for pysh.

Handles bootstrapping and configuring the user's Python environment
at ~/.config/pysh/.venv (or custom PYSH_CONFIG_DIR).
"""

from __future__ import annotations

import re
import site
import sys
import tomllib
from pathlib import Path

from .environment import env
from .model import Command, ShellResult

__all__ = [
    'get_venv_path',
    'initialize_shell_venv',
    'install_packages',
    'uninstall_packages',
    'sync_packages',
    'list_packages',
]


def _find_project_pyproject() -> Path:
    """Find the project's pyproject.toml by walking up from this file."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        pyproject = current / 'pyproject.toml'
        if pyproject.exists():
            return pyproject
        current = current.parent
    raise FileNotFoundError('Could not find pyproject.toml in parent directories')


def _strip_version(dep: str) -> str:
    """Strip version specifier from a dependency string (e.g., 'jedi>=0.19.2' -> 'jedi')."""
    # Match package name before any version specifier or extras
    match = re.match(r'^([a-zA-Z0-9][-a-zA-Z0-9_.]*)', dep)
    return match.group(1) if match else dep


def _get_shell_dependencies() -> list[str]:
    """Read shell dependencies from project's pyproject.toml (without version specifiers)."""
    pyproject_path = _find_project_pyproject()
    with open(pyproject_path, 'rb') as f:
        data = tomllib.load(f)
    deps = data.get('project', {}).get('dependencies', [])
    return [_strip_version(dep) for dep in deps]


# Initial pyproject.toml content for new config directories
_INITIAL_PYPROJECT = """\
[project]
name = "pysh-user-packages"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []
"""


def get_venv_path() -> Path:
    """Get the path to the user's venv."""
    return env.config_dir / '.venv'


def _get_python_version_from_venv(venv_path: Path) -> tuple[int, int] | None:
    """Read Python version from venv's pyvenv.cfg."""
    cfg = venv_path / 'pyvenv.cfg'
    if not cfg.exists():
        return None

    for line in cfg.read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            if key.strip() == 'version_info':
                parts = value.strip().split('.')
                if len(parts) >= 2:
                    return int(parts[0]), int(parts[1])
    return None


def _get_site_packages(venv_path: Path) -> Path | None:
    """Get the site-packages directory for a venv."""
    version = _get_python_version_from_venv(venv_path)
    if version is None:
        return None
    major, minor = version
    return venv_path / 'lib' / f'python{major}.{minor}' / 'site-packages'


def _configure_sys_path(venv_path: Path) -> None:
    """Configure sys.path to use the user venv."""
    site_packages = _get_site_packages(venv_path)
    if site_packages is None:
        raise RuntimeError(f'Cannot determine site-packages for {venv_path}')

    # Remove any existing site-packages from sys.path
    # (but keep the shell's source directory)
    sys.path = [p for p in sys.path if 'site-packages' not in p]

    # Add user venv's site-packages at high priority
    sys.path.insert(0, str(site_packages))

    # Let site module process .pth files
    site.addsitedir(str(site_packages))

    # Update sys.prefix to point to user venv
    sys.prefix = str(venv_path)
    sys.base_prefix = str(venv_path)


def _create_config_dir(config_dir: Path) -> None:
    """Create the config directory and initial pyproject.toml if needed."""
    config_dir.mkdir(parents=True, exist_ok=True)

    pyproject_path = config_dir / 'pyproject.toml'
    if not pyproject_path.exists():
        pyproject_path.write_text(_INITIAL_PYPROJECT)


def _create_venv(venv_path: Path) -> ShellResult:
    """Create a new venv at the given path using uv."""
    vi = sys.version_info
    return Command('uv', 'venv', str(venv_path), '--python', f'{vi.major}.{vi.minor}').env(
        VIRTUAL_ENV=''
    )()


def _ensure_shell_deps(config_dir: Path) -> None:
    """Ensure shell dependencies are in pyproject.toml and installed."""
    dir_arg = str(config_dir)
    shell_deps = _get_shell_dependencies()

    # Add all shell deps and sync (uv add syncs by default)
    # Clear VIRTUAL_ENV to avoid uv warning about mismatched environments
    Command('uv', 'add', '--directory', dir_arg, *shell_deps).env(VIRTUAL_ENV='')()


def initialize_shell_venv() -> None:
    """
    Initialize the user Python environment.

    Creates ~/.config/pysh/.venv if needed, ensures shell dependencies
    are installed, and configures sys.path to use it.
    """
    config_dir = env.config_dir
    venv_path = get_venv_path()

    # Create config directory and pyproject.toml if needed
    _create_config_dir(config_dir)

    # Create venv if it doesn't exist
    if not venv_path.exists():
        print(f'Creating user environment at {venv_path}...')
        result = _create_venv(venv_path)
        if not result:
            raise RuntimeError(f'Failed to create venv at {venv_path}')

    # Ensure shell dependencies are installed
    _ensure_shell_deps(config_dir)

    # Configure sys.path to use user venv
    _configure_sys_path(venv_path)


def install_packages(*names: str) -> ShellResult:
    """
    Install packages and add them to user's pyproject.toml.

    Args:
        *names: Package names (with optional version specifiers)

    Returns:
        ShellResult indicating success/failure

    Example:
        install_packages("requests", "numpy>=1.26")
    """
    config_dir = env.config_dir
    return Command('uv', 'add', '--directory', str(config_dir), *names).env(VIRTUAL_ENV='')()


def uninstall_packages(*names: str) -> ShellResult:
    """
    Uninstall packages and remove from user's pyproject.toml.

    Args:
        *names: Package names to remove

    Returns:
        ShellResult indicating success/failure

    Example:
        uninstall_packages("requests", "numpy")
    """
    config_dir = env.config_dir
    return Command('uv', 'remove', '--directory', str(config_dir), *names).env(VIRTUAL_ENV='')()


def sync_packages() -> ShellResult:
    """
    Sync venv with pyproject.toml.

    Useful after modifying pyproject.toml directly, or to restore
    packages after deleting the venv.

    Returns:
        ShellResult indicating success/failure
    """
    config_dir = env.config_dir
    return Command('uv', 'sync', '--directory', str(config_dir)).env(VIRTUAL_ENV='')()


def list_packages() -> ShellResult:
    """
    List installed packages in the user's venv.

    Returns:
        ShellResult indicating success/failure
    """
    venv_path = get_venv_path()
    return Command('uv', 'pip', 'list').env(VIRTUAL_ENV=str(venv_path))()
