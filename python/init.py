"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""

# Bare-bones initialization - import shp for shell primitives
from shp import *
import shp

# Included imports
# Use sparingly - we would prefer people opt in to these in their own configuration files
import os
from pathlib import Path

# Import core Python shell features (like source())
from core import *

# Import ergonomic wrappers for shell builtins
from shp.ergo.builtins import *
