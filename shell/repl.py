"""
REPL implementation using GNU readline callback interface.

This module provides an event-loop-friendly REPL that can be extended
to handle other I/O sources alongside stdin.
"""

from __future__ import annotations

import codeop
import select
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

from shell import completion, rl
from shell.environment import env
from shell.model import Program, ShellResult, ShellRunnable
from shell.trap import TrapType

# History file: ~/.pysh_history by default, override with PYSH_HISTFILE
DEFAULT_HISTFILE = Path.home() / '.pysh_history'


def get_history_file() -> Path:
    """Get the history file path from environment or default."""
    histfile = env.get('PYSH_HISTFILE', DEFAULT_HISTFILE)
    path = histfile if isinstance(histfile, Path) else Path(histfile)
    return path.expanduser()


class REPLHooks:
    """Manages REPL execution lifecycle hooks."""

    def __init__(self):
        self.before_execute: list[Callable[[str], None]] = []
        self.after_execute: list[Callable[[str], None]] = []

    def on_before_execute(self, callback: Callable[[str], None]) -> Callable[[str], None]:
        """Register a callback to run before executing code."""
        self.before_execute.append(callback)
        return callback

    def on_after_execute(self, callback: Callable[[str], None]) -> Callable[[str], None]:
        """Register a callback to run after executing code."""
        self.after_execute.append(callback)
        return callback

    def off_before_execute(self, callback: Callable[[str], None]) -> bool:
        """Remove a before_execute callback."""
        try:
            self.before_execute.remove(callback)
            return True
        except ValueError:
            return False

    def off_after_execute(self, callback: Callable[[str], None]) -> bool:
        """Remove an after_execute callback."""
        try:
            self.after_execute.remove(callback)
            return True
        except ValueError:
            return False

    def fire_before_execute(self, code: str):
        """Fire all before_execute hooks."""
        for hook in self.before_execute:
            try:
                hook(code)
            except Exception as e:
                print(f'Error in REPL hook: {e}', file=sys.stderr)

    def fire_after_execute(self, code: str):
        """Fire all after_execute hooks."""
        for hook in self.after_execute:
            try:
                hook(code)
            except Exception as e:
                print(f'Error in REPL hook: {e}', file=sys.stderr)


# Global hooks instance
hooks = REPLHooks()


def _is_expression(code: str) -> bool:
    """Check if code is a valid expression (not a statement)."""
    try:
        compile(code, '<string>', 'eval')
        return True
    except SyntaxError:
        return False


def _execute_code(code: str, globals_dict: dict) -> None:
    """Execute a code string with full lifecycle (hooks + error handling).

    Auto-runs ShellRunnable objects if they're the result of an expression.
    Raises SystemExit if exit() is called.
    """
    hooks.fire_before_execute(code)

    try:
        if _is_expression(code):
            result = eval(code, globals_dict)

            # Auto-run ShellRunnable and Program objects
            if isinstance(result, ShellRunnable):
                result()
            elif isinstance(result, Program):
                result()()  # Call with no args, then run
            elif result is not None and not isinstance(result, ShellResult):
                print(repr(result))
        else:
            exec(code, globals_dict)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print('^C')
    except Exception:
        traceback.print_exc()
    finally:
        hooks.fire_after_execute(code)


class REPL:
    """Event-loop-friendly REPL using readline callback interface."""

    def __init__(self):
        self._compiler = codeop.CommandCompiler()
        self._buffer = ''
        self._running = False
        self._pending_line: str | None = None  # Line received from callback
        self._got_eof = False

        # Get __main__ namespace at construction time
        import __main__  # noqa: PLC0415

        self._globals = __main__.__dict__

    @staticmethod
    def _resolve_prompt(value: str | Callable[[], str], default: str) -> str:
        """Resolve a prompt value: call if callable, use as string otherwise."""
        try:
            return value() if callable(value) else str(value)
        except Exception:
            return default

    @property
    def _current_prompt(self) -> str:
        if self._buffer:
            return self._resolve_prompt(env.ps2, '..... ')
        return self._resolve_prompt(env.ps1, 'ship> ')

    def _on_line(self, line: str | None) -> None:
        """Callback invoked by readline when a complete line is entered."""
        self._pending_line = line

    def _on_eof(self) -> None:
        """Callback for EOF (Ctrl-D on empty line)."""
        self._got_eof = True

    def _process_line(self, line: str) -> bool:
        """Process a complete line. Returns True if REPL should exit."""
        # Accumulate into buffer
        if self._buffer:
            self._buffer += '\n' + line
        else:
            self._buffer = line

        # Skip empty input
        if not self._buffer.strip():
            self._buffer = ''
            return False

        # Check if statement is complete
        try:
            code_obj = self._compiler(self._buffer)
        except SyntaxError as e:
            print(f'SyntaxError: {e}')
            self._buffer = ''
            return False

        if code_obj is None:
            # Incomplete - need more input
            return False

        # Complete statement - execute it
        code = self._buffer
        self._buffer = ''

        try:
            _execute_code(code, self._globals)
        except SystemExit:
            return True

        return False

    def run(self) -> None:
        """Run the REPL main loop."""
        print('ShipShell Python REPL')
        print("Type 'exit()' or press Ctrl+D to quit")
        print()

        # Load history
        histfile = get_history_file()
        try:
            rl.read_history(str(histfile))
        except OSError as e:
            print(f'Warning: could not load history: {e}', file=sys.stderr)

        # Configure tab completion
        completion.setup()

        self._running = True

        try:
            while self._running:
                # Install handler and show prompt
                rl.install_line_handler(self._current_prompt, self._on_line, self._on_eof)

                try:
                    # Read characters until we get a complete line
                    self._pending_line = None
                    self._got_eof = False

                    while self._pending_line is None and not self._got_eof:
                        try:
                            readable, _, _ = select.select([sys.stdin], [], [])
                        except KeyboardInterrupt:
                            # Ctrl-C: remove handler, move to new line, clear buffer
                            rl.remove_handler()
                            print('\n^C')
                            self._buffer = ''
                            break

                        if sys.stdin in readable:
                            rl.read_char()

                finally:
                    # Always remove handler before any output
                    rl.remove_handler()

                # Handle EOF
                if self._got_eof:
                    self._running = False
                    continue

                # Handle Ctrl-C (pending_line is None, no EOF)
                if self._pending_line is None:
                    continue

                # Add non-empty lines to history
                if self._pending_line.strip():
                    rl.add_history(self._pending_line)

                # Process the line (terminal is now in normal state)
                if self._process_line(self._pending_line):
                    self._running = False

        finally:
            # Save history
            try:
                rl.write_history(str(histfile))
            except OSError as e:
                print(f'Warning: could not save history: {e}', file=sys.stderr)

            print('Exiting...')

            # Fire EXIT trap
            try:
                env.traps.fire(TrapType.EXIT)
            except Exception:
                pass
            env.traps.cleanup()


def run_repl():
    """Convenience function to create and run a REPL."""
    repl = REPL()
    repl.run()
