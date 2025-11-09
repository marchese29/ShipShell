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

# The only module we're going to import on the user's behalf is the ergo support or built-ins since
# it is fundamental to the shell.  Everything else can be done in an RC file as desired.
from shp.ergo.builtins import *
