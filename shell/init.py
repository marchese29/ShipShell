"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""


# Load user init file if it exists (after imports so init file has access to everything)
def _shp_initialize():
    import shp
    from core import source

    from pathlib import Path
    import sys

    # Get (or set) the SHIP_CONFIG_DIR
    shp_config_dir: Path | None = shp.env.get("SHIP_CONFIG_DIR", None)
    if shp_config_dir is None:
        shp_config_dir = Path.home() / ".config" / "ship"
        shp.env["SHIP_CONFIG_DIR"] = shp_config_dir

    # We'll look either in the ship configuration directory, or the home directory
    config_init = shp_config_dir / "init.py"
    home_init = Path.home() / "init.py"

    try:
        if config_init.exists():
            source(config_init)
        elif home_init.exists():
            source(home_init)
    except Exception as e:
        # Log the error but continue - don't crash the shell on bad rc file
        print(f"Error loading init file: {e}", file=sys.stderr)


_shp_initialize()

# Remove so we don't pollute the global namespace
del _shp_initialize
