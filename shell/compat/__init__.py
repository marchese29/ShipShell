"""
Compatibility layer for running non-Python shell scripts.
"""

from .bash import BashInterpreter, run_bash_code

__all__ = ['BashInterpreter', 'run_bash_code']
