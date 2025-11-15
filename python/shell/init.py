"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""


# Load user init file if it exists (after imports so init file has access to everything)
def load_user_init_file():
    from core import source

    from pathlib import Path
    import sys

    config_init = Path.home() / ".config" / "ship" / "init.py"
    home_init = Path.home() / "init.py"

    try:
        if config_init.exists():
            source(config_init)
        elif home_init.exists():
            source(home_init)
    except Exception as e:
        # Log the error but continue - don't crash the shell on bad rc file
        print(f"Error loading init file: {e}", file=sys.stderr)


# Load RC file now that everything is imported
load_user_init_file()

# Remove things we don't want exposed in the global namespace
del load_user_init_file
