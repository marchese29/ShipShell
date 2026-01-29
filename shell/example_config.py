"""
Example config.py for pysh.

This file runs BEFORE the Python environment is set up.
Copy this to ~/.config/pysh/config.py to use.

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

from shell.environment import env

# Uncomment to customize config directory location:
# env["PYSH_CONFIG_DIR"] = Path.home() / ".pysh"

# Set basic environment variables
env['EDITOR'] = 'nvim'
