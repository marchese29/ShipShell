"""
Utilities for working with the virtual environment for the shell's python interpreter
"""

import site
import sys
from pathlib import Path

import shp
from shp.builtins import which


__all__ = ["initialize_shell_venv", "install_packages"]


def uv(*args: str) -> shp.ShipRunnable:
    # See if uv is already in the path
    if which("uv", silent=True)().exit_code != 0:
        raise NotImplementedError("For now, you must have uv installed")
    return shp.prog("uv")(*args)


def get_python_version(venv_path: Path) -> str | None:
    pyenv_file = venv_path / "pyvenv.cfg"
    if pyenv_file.exists() and pyenv_file.is_file():
        with open(pyenv_file) as f:
            for line in f.read().split("\n"):
                items = line.split("=")
                if len(items) == 2 and items[0].strip() == "version_info":
                    return items[1].strip()
    return None


def initialize_shell_venv():
    """
    Checks that the virtual environment for the shell is set up (and sets it up if not
    present)
    """
    # 1. The path to the environment either comes from ENV or ~/.config/ship/venv
    venv_path: Path = (shp.env["SHIP_CONFIG_DIR"] / "venv").resolve()

    # 2. Ensure the environment exists
    if not venv_path.exists():
        vi = sys.version_info
        uv("venv", str(venv_path), "--python", f"{vi.major}.{vi.minor}")()

    # 3. Remove whatever default site-packages directories are already in the path
    sys.path = list(filter(lambda path: "site-packages" not in path, sys.path))

    # 4. Set up path lookups
    pv = get_python_version(venv_path)
    if pv is None:
        raise RuntimeError("venv is missing python version from pyvenv.cfg")
    pv = pv.split(".")
    site_packages_dir = str(
        venv_path / "lib" / f"python{pv[0]}.{pv[1]}" / "site-packages"
    )
    sys.path.insert(0, site_packages_dir)

    # 5. Make sys.prefix look like the environment root
    sys.prefix = str(venv_path)
    sys.base_prefix = str(venv_path)

    # 6. Let 'site' process .pth files and etc.
    site.addsitedir(site_packages_dir)


def install_packages(*names: str) -> shp.ShipResult:
    """
    Installs the packages with the given names into the shell's python environment.  This
    allows you to install arbitrary python libraries from PyPI
    """
    venv_path = str(shp.env["SHIP_CONFIG_DIR"] / "venv")
    return uv("pip", "install", *names).with_env(VIRTUAL_ENV=venv_path)()
