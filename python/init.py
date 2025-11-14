"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""

# Add the main shp module and core directly to the namespace
from shp import *  # noqa: F403
from core import *  # noqa: F403

# Import ergonomic wrappers for shell builtins and POSIX utilities
from shp.ergo.builtins import *  # noqa: F403
from shp.ergo.posix import *  # noqa: F403


# Load user init file if it exists (after imports so init file has access to everything)
def load_user_init_file():
    from core import source
    from shp import env

    from pathlib import Path
    import sys

    # Respect XDG_CONFIG_HOME, falling back to ~/.config
    xdg_config_home = env.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        config_init = Path(xdg_config_home) / "ship" / "init.py"
    else:
        config_init = Path.home() / ".config" / "ship" / "init.py"

    home_init = Path.home() / "init.py"

    try:
        if config_init.exists():
            source(config_init)
        elif home_init.exists():
            source(home_init)
    except Exception as e:
        # Log the error but continue - don't crash the shell on bad rc file
        print(f"Error loading rc file: {e}", file=sys.stderr)


# Load RC file now that everything is imported
load_user_init_file()

# Remove things we don't want exposed in the global namespace
del load_user_init_file
