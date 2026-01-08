"""
Utilities for working with the virtual environment for the shell's python interpreter
"""

from pathlib import Path

import shp


def ship_config_dir() -> Path:
    if "SHIP_CONFIG_DIR" not in shp.env:
        shp.env["SHIP_CONFIG_DIR"] = Path.home() / ".config" / "ship"
    return shp.env["SHIP_CONFIG_DIR"]


def initialize_shell_venv(): ...


def install_packages(*names: str) -> shp.ShipResult: ...
