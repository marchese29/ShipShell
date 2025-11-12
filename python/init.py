"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""

# Add the main shp module and core directly to the namespace
from shp import *  # noqa: F403
from core import *  # noqa: F403

# Import ergonomic wrappers for shell builtins
from shp.ergo.builtins import *  # noqa: F403


# Load user rc file if it exists (after imports so rc file has access to everything)
def _load_rc_file():
    from core import source
    from shp import env

    from pathlib import Path
    import sys

    # Respect XDG_CONFIG_HOME, falling back to ~/.config
    _xdg_config_home = env.get("XDG_CONFIG_HOME")
    if _xdg_config_home:
        _config_init = Path(_xdg_config_home) / "ship" / "init.py"
    else:
        _config_init = Path.home() / ".config" / "ship" / "init.py"

    _home_init = Path.home() / "init.py"

    try:
        if _config_init.exists():
            source(_config_init)
        elif _home_init.exists():
            source(_home_init)
    except Exception as e:
        # Log the error but continue - don't crash the shell on bad rc file
        print(f"Error loading rc file: {e}", file=sys.stderr)


# Load RC file now that everything is imported
_load_rc_file()

# Remove things we don't want exposed in the global namespace
del _load_rc_file
