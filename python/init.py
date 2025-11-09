"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""

# Allow easy access to shp bindings
from shp import *
import shp

# Included imports
# Use sparingly - we would prefer people opt in to these in their own configuration files
import os
from pathlib import Path

# ship_ergo.builtins is dual-purpose.  People can import it in their scripts for a better IDE
# editing experience; and we also use it as the actual source implementation
from ship_ergo.builtins import *
