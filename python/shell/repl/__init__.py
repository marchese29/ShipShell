"""
Python-based REPL implementation.

This module provides the core REPL interface with execution hooks.
For the default prompt-based implementation, see repl.default.
"""

import sys
import traceback
from typing import Callable, Iterator, Optional


class REPLHooks:
    """Manages REPL execution lifecycle hooks."""

    def __init__(self):
        self.before_execute: list[Callable[[str], None]] = []
        self.after_execute: list[Callable[[str], None]] = []

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

    def fire_before_execute(self, code: str):
        """Fire all before_execute hooks."""
        self._fire_hooks(self.before_execute, code)

    def fire_after_execute(self, code: str):
        """Fire all after_execute hooks."""
        self._fire_hooks(self.after_execute, code)


# Global hooks instance
hooks = REPLHooks()


def _is_expression(code: str) -> bool:
    """Check if code is a valid expression (not a statement)."""
    try:
        compile(code, "<string>", "eval")
        return True
    except SyntaxError:
        return False


def _execute_code(code: str, globals_dict: dict) -> None:
    """Execute a code string with full lifecycle (hooks + error handling).

    Auto-runs ShipRunnable objects if they're the result of an expression.
    Raises SystemExit if exit() is called.
    """
    import shp

    # Fire before_execute hook
    hooks.fire_before_execute(code)

    try:
        # Try eval for expressions first
        if _is_expression(code):
            result = eval(code, globals_dict)

            # Auto-run ShipRunnable objects
            if isinstance(result, shp.ShipRunnable):
                result()
            elif result is not None:
                print(repr(result))
        else:
            # Execute as statement
            exec(code, globals_dict)
    except SystemExit:
        # Re-raise to let caller handle REPL exit
        raise
    except KeyboardInterrupt:
        print("^C")
    except Exception:
        traceback.print_exc()
    finally:
        # Always fire after_execute hook (even on error)
        hooks.fire_after_execute(code)


def run_repl(input_source: Optional[Iterator[str]] = None):
    """Main REPL loop.

    Args:
        input_source: Optional generator/iterator that yields code strings.
                     If None, uses the default prompt-based input from repl.default.
    """
    # Get the main module's namespace for REPL execution
    import __main__

    repl_globals = __main__.__dict__

    # Use default prompt-based input if no custom source provided
    if input_source is None:
        # Install dependencies and import default implementation
        from shp.py_env import install_packages

        install_packages("prompt_toolkit")
        install_packages("pygments")
        install_packages("jedi")

        from repl.default import prompt

        input_source = prompt()

    # Process each code string from the input source
    try:
        for code in input_source:
            if code.strip():
                try:
                    _execute_code(code, repl_globals)
                except SystemExit:
                    print("Exiting...")
                    return
    except KeyboardInterrupt:
        # Ctrl+C during iteration
        print("\n^C")
    except EOFError:
        # Ctrl+D or end of input
        print("Exiting...")


if __name__ == "__main__":
    # Allow running this module directly for testing
    run_repl()
