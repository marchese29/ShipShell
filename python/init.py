"""
ShipShell initialization code.

NOTE: This code is run directly in the shell REPL - whatever you do in here will apply to the whole
shell environment.
"""

# shp module is defined natively in rust
from shp import *

# Import shell built-in functions from ship_ergo
# Using "from ... import *" only imports names in __all__, keeping typing imports out of namespace
from ship_ergo.builtins import *
