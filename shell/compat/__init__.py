"""
Compatibility layer for running non-Python shell scripts.
"""

from .bash import ShipBashInterpreter, run_bash_code

__all__ = ['ShipBashInterpreter', 'run_bash_code']
