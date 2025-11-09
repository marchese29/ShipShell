"""
Marker module indicating code is running in ShipShell.

This module only exists in the ShipShell environment and is used
for IDE-friendly detection of the ShipShell runtime.

Usage in scripts:
    try:
        import ship_shell_marker
    except ImportError:
        from ship_ergo import *
"""
