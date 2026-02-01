"""
Compatibility layer for running bash code with state synchronization.
"""

from .bash import BASH_PATH, BashSource, source_bash

__all__ = ['BASH_PATH', 'BashSource', 'source_bash']
