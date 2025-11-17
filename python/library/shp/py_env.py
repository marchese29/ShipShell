"""
Utilities for working with the virtual environment for the shell's python interpreter
"""

import shp


def initialize_shell_venv():
    """
    Checks that the virtual environment for the shell is set up (and sets it up if not
    present)
    """
    ...


def install_packages(*names: str) -> shp.ShipResult:
    """
    Installs the packages with the given names into the shell's python environment.  This
    allows you to install arbitrary python libraries from PyPI
    """
    ...
