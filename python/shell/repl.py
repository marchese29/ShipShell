"""
Python-based REPL implementation using prompt_toolkit.

This replaces the previous Rust-based REPL, making the shell more maintainable
and customizable directly in Python.
"""

import sys
from typing import Callable


class REPLHooks:
    """Manages REPL lifecycle hooks."""

    def __init__(self):
        self.before_prompt: list[Callable[[], None]] = []
        self.before_continuation: list[Callable[[str, str], None]] = []
        self.before_execute: list[Callable[[str], None]] = []
        self.after_execute: list[Callable[[str], None]] = []

    def on_before_prompt(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback to run before showing the primary prompt.

        Returns the callback for easy removal later.
        """
        self.before_prompt.append(callback)
        return callback

    def on_before_continuation(
        self, callback: Callable[[str, str], None]
    ) -> Callable[[str, str], None]:
        """Register a callback to run before showing continuation prompt.

        Callback receives (prev_prompt: str, buffer: str)
        Returns the callback for easy removal later.
        """
        self.before_continuation.append(callback)
        return callback

    def on_before_execute(
        self, callback: Callable[[str], None]
    ) -> Callable[[str], None]:
        """Register a callback to run before executing code.

        Callback receives the code string.
        Returns the callback for easy removal later.
        """
        self.before_execute.append(callback)
        return callback

    def on_after_execute(
        self, callback: Callable[[str], None]
    ) -> Callable[[str], None]:
        """Register a callback to run after executing code.

        Callback receives the code string that was executed.
        Returns the callback for easy removal later.
        """
        self.after_execute.append(callback)
        return callback

    def off_before_prompt(self, callback: Callable[[], None]) -> bool:
        """Remove a before_prompt callback. Returns True if removed, False if not found."""
        try:
            self.before_prompt.remove(callback)
            return True
        except ValueError:
            return False

    def off_before_continuation(self, callback: Callable[[str, str], None]) -> bool:
        """Remove a before_continuation callback. Returns True if removed, False if not found."""
        try:
            self.before_continuation.remove(callback)
            return True
        except ValueError:
            return False

    def off_before_execute(self, callback: Callable[[str], None]) -> bool:
        """Remove a before_execute callback. Returns True if removed, False if not found."""
        try:
            self.before_execute.remove(callback)
            return True
        except ValueError:
            return False

    def off_after_execute(self, callback: Callable[[str], None]) -> bool:
        """Remove an after_execute callback. Returns True if removed, False if not found."""
        try:
            self.after_execute.remove(callback)
            return True
        except ValueError:
            return False

    def _fire_hooks(self, hook_list: list[Callable], *args):
        """Fire all hooks in a list with consistent error handling."""
        for hook in hook_list:
            try:
                hook(*args)
            except Exception as e:
                print(f"Error in REPL hook: {e}", file=sys.stderr)

    def fire_before_prompt(self):
        """Fire all before_prompt hooks."""
        self._fire_hooks(self.before_prompt)

    def fire_before_continuation(self, prev_prompt: str, buffer: str):
        """Fire all before_continuation hooks."""
        self._fire_hooks(self.before_continuation, prev_prompt, buffer)

    def fire_before_execute(self, code: str):
        """Fire all before_execute hooks."""
        self._fire_hooks(self.before_execute, code)

    def fire_after_execute(self, code: str):
        """Fire all after_execute hooks."""
        self._fire_hooks(self.after_execute, code)


class REPLState:
    """Stores REPL prompt configuration."""

    def __init__(self):
        self.primary_prompt = "ship> "
        self.continuation_prompt = "..... "
        self.right_prompt = ""


def run_repl():
    """Main REPL loop using prompt_toolkit."""

    # TODO: Check if user provided custom input implementation

    # Install and import prompt_toolkit (after environment is initialized)
    from shp.py_env import install_packages

    install_packages("prompt_toolkit")

    import _repl_internal

    hooks = REPLHooks()
    state = REPLState()
    _repl_internal.loop(hooks, state)


if __name__ == "__main__":
    # Allow running this module directly for testing
    run_repl()
