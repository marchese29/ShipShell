"""
User initialization for pysh.

This module handles loading user configuration and initialization files
in two phases:

1. initialize_config() - Runs BEFORE the Python environment is set up.
   Loads config.py which can only use basic Python and env manipulation.

2. initialize_user() - Runs AFTER the Python environment is set up.
   Loads user.py with full shell functionality available.
"""

import sys
from pathlib import Path

from .builtins import source
from .environment import env


def _find_config_file() -> Path | None:
    """
    Find the config.py file.

    Search order:
    1. PYSH_CONFIG_DIR/config.py (if PYSH_CONFIG_DIR is set)
    2. ~/.config/pysh/config.py (default location)

    Returns:
        Path to config.py if found, None otherwise
    """
    config_path = env.config_dir / 'config.py'

    if config_path.is_file():
        return config_path

    return None


def _find_user_file() -> Path | None:
    """
    Find the user.py init file.

    Looks in the current PYSH_CONFIG_DIR (which may have been
    modified by config.py).

    Returns:
        Path to user.py if found, None otherwise
    """
    user_path = env.config_dir / 'user.py'

    if user_path.is_file():
        return user_path

    return None


def initialize_config() -> None:
    """
    Phase 1: Load config.py to allow user to set PYSH_CONFIG_DIR.

    Runs config.py blindly - user is responsible for only using
    functionality available at this stage.

    Available at this stage:
        - env (shell environment)
        - Path, os, basic Python stdlib
        - Setting environment variables

    NOT available at this stage:
        - Shell commands (prog, Command, etc.)
        - Installed packages (jedi, prompt_toolkit, etc.)
        - Builtins that run commands

    Use this to:
        - Set PYSH_CONFIG_DIR to customize config location
        - Set basic environment variables (EDITOR, etc.)
    """
    config_path = _find_config_file()

    if config_path is not None:
        try:
            source(config_path)
        except Exception as e:
            # Log error but continue - don't crash on bad config file
            print(f'Error loading config file: {e}', file=sys.stderr)


def initialize_user() -> None:
    """
    Phase 2: Load user.py after venv is ready.

    Full shell functionality is available at this stage:
        - All shell commands and builtins
        - Installed packages (jedi, prompt_toolkit, etc.)
        - py_env.install_packages() for adding more packages
        - shell.wiring: wire_module(), wire_path_programs() for ergonomics
    """
    user_path = _find_user_file()

    if user_path is not None:
        try:
            source(user_path)
        except Exception as e:
            # Log error but continue - don't crash on bad init file
            print(f'Error loading user init file: {e}', file=sys.stderr)
