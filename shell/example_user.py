"""
Example user.py for pysh.

This file runs AFTER the Python environment is set up.
Copy this to ~/.config/pysh/user.py to use.

Full shell functionality is available at this stage:
    - All shell commands and builtins
    - Installed packages (jedi, prompt_toolkit, etc.)
    - py_env.install_packages() for adding more packages
    - shell.wiring: wire_module(), wire_path_programs() for ergonomics
"""

from pathlib import Path

from shell import env
from shell.wiring import wire_module, wire_path_programs

#####################
# Environment setup #
#####################

# Homebrew paths
env['HOMEBREW_PREFIX'] = Path('/opt/homebrew')
env['HOMEBREW_CELLAR'] = Path('/opt/homebrew/Cellar')
env['HOMEBREW_REPOSITORY'] = Path('/opt/homebrew')
env.path.insert(0, Path('/opt/homebrew/bin'))
env.path.insert(0, Path('/opt/homebrew/sbin'))

# Cargo (Rust)
env.path.insert(0, Path.home() / '.cargo' / 'bin')


##############
# ERGONOMICS #
##############


def wire():
    """
    Wire up all ergonomic calling functions.

    Puts everything in a "c" module so you don't have thousands of
    things in your global namespace.

    Usage after wiring:
        c.ls('-la')
        c.cd('/tmp')
        c.pwd()
    """
    wire_path_programs('c')
    wire_module('shell.builtins', 'c')
    wire_module('shell.model', 'c')

    # Package management goes into the "p" namespace
    wire_module('shell.py_env', 'p')


wire()
