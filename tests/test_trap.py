"""Tests for the shell trap system.

Covers:
- TrapType enum parsing and properties
- TrapManager handler lifecycle
- Trap helper builtins (trap_list, trap_show)
- DEBUG/TRACE/ERR trap firing
- EXIT trap in various contexts
- Subshell trap isolation
"""

import signal
import sys

import pytest

from shell.builtins import trap_list, trap_show
from shell.environment import env
from shell.model import (
    InProcessCallable,
    ShellResult,
    Subshell,
    capture,
    prog,
)
from shell.trap import TrapManager, TrapType


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment and clean up traps for each test."""
    env.initialize()
    # Clear any existing traps and create a fresh TrapManager
    env.traps.cleanup()
    env._traps = TrapManager()
    yield
    # Cleanup after test
    env.traps.cleanup()


# =============================================================================
# TrapType Enum Tests
# =============================================================================


class TestTrapTypeEnum:
    """Tests for TrapType enum parsing and properties."""

    def test_synthetic_traps_have_negative_values(self):
        """Synthetic traps have negative values (no OS signal number)."""
        assert TrapType.DEBUG.value < 0
        assert TrapType.TRACE.value < 0
        assert TrapType.ERR.value < 0
        assert TrapType.EXIT.value < 0
        assert TrapType.RETURN.value < 0
        # Each has a unique value
        values = {TrapType.DEBUG.value, TrapType.TRACE.value, TrapType.ERR.value,
                  TrapType.EXIT.value, TrapType.RETURN.value}
        assert len(values) == 5

    def test_signal_traps_have_signal_numbers(self):
        """Signal traps have their OS signal numbers as values."""
        assert TrapType.SIGINT.value == signal.SIGINT
        assert TrapType.SIGTERM.value == signal.SIGTERM
        assert TrapType.SIGHUP.value == signal.SIGHUP

    def test_is_signal_property(self):
        """is_signal correctly identifies signal vs synthetic traps."""
        assert TrapType.SIGINT.is_signal is True
        assert TrapType.SIGTERM.is_signal is True
        assert TrapType.DEBUG.is_signal is False
        assert TrapType.EXIT.is_signal is False

    def test_is_synthetic_property(self):
        """is_synthetic correctly identifies synthetic traps."""
        assert TrapType.DEBUG.is_synthetic is True
        assert TrapType.EXIT.is_synthetic is True
        assert TrapType.SIGINT.is_synthetic is False

    def test_from_string_synthetic_names(self):
        """from_string parses synthetic trap names."""
        assert TrapType.from_string('DEBUG') == TrapType.DEBUG
        assert TrapType.from_string('TRACE') == TrapType.TRACE
        assert TrapType.from_string('ERR') == TrapType.ERR
        assert TrapType.from_string('EXIT') == TrapType.EXIT
        assert TrapType.from_string('RETURN') == TrapType.RETURN

    def test_from_string_case_insensitive(self):
        """from_string is case insensitive."""
        assert TrapType.from_string('debug') == TrapType.DEBUG
        assert TrapType.from_string('Debug') == TrapType.DEBUG
        assert TrapType.from_string('exit') == TrapType.EXIT

    def test_from_string_signal_with_sig_prefix(self):
        """from_string parses signal names with SIG prefix."""
        assert TrapType.from_string('SIGINT') == TrapType.SIGINT
        assert TrapType.from_string('SIGTERM') == TrapType.SIGTERM
        assert TrapType.from_string('SIGHUP') == TrapType.SIGHUP

    def test_from_string_signal_without_sig_prefix(self):
        """from_string parses signal names without SIG prefix."""
        assert TrapType.from_string('INT') == TrapType.SIGINT
        assert TrapType.from_string('TERM') == TrapType.SIGTERM
        assert TrapType.from_string('HUP') == TrapType.SIGHUP

    def test_from_string_exit_as_zero(self):
        """from_string parses '0' as EXIT (bash compatibility)."""
        assert TrapType.from_string('0') == TrapType.EXIT

    def test_from_string_unknown_raises(self):
        """from_string raises ValueError for unknown trap names."""
        with pytest.raises(ValueError, match='Unknown trap'):
            TrapType.from_string('UNKNOWN')
        with pytest.raises(ValueError, match='Unknown trap'):
            TrapType.from_string('SIGFOO')


# =============================================================================
# TrapManager Tests
# =============================================================================


class TestTrapManager:
    """Tests for TrapManager handler management."""

    def test_set_and_get(self):
        """Can set and retrieve a trap handler."""
        manager = TrapManager()
        handler = InProcessCallable(lambda: 0)

        manager.set(TrapType.EXIT, handler)
        assert manager.get(TrapType.EXIT) is handler

    def test_set_auto_wraps_callable(self):
        """Plain callables are auto-wrapped in InProcessCallable."""
        manager = TrapManager()
        called = []

        # Pass a plain callable, not an InProcessCallable
        manager.set(TrapType.EXIT, lambda: called.append(True) or 0)

        # Should be wrapped and executable
        handler = manager.get(TrapType.EXIT)
        assert handler is not None
        assert isinstance(handler, InProcessCallable)

        # Should work when fired
        manager.fire(TrapType.EXIT)
        assert called == [True]

    def test_get_unset_returns_none(self):
        """get returns None for unset traps."""
        manager = TrapManager()
        assert manager.get(TrapType.EXIT) is None
        assert manager.get(TrapType.DEBUG) is None

    def test_set_none_clears(self):
        """Setting a trap to None clears it."""
        manager = TrapManager()
        handler = InProcessCallable(lambda: 0)

        manager.set(TrapType.EXIT, handler)
        assert manager.get(TrapType.EXIT) is handler

        manager.set(TrapType.EXIT, None)
        assert manager.get(TrapType.EXIT) is None

    def test_fire_executes_handler(self):
        """fire executes the handler and returns exit code."""
        manager = TrapManager()
        called = []

        def handler():
            called.append(True)
            return 0

        manager.set(TrapType.EXIT, InProcessCallable(handler))
        result = manager.fire(TrapType.EXIT)

        assert called == [True]
        assert result == 0

    def test_fire_returns_none_when_unset(self):
        """fire returns None when no handler is set."""
        manager = TrapManager()
        result = manager.fire(TrapType.EXIT)
        assert result is None

    def test_fire_reentrancy_guard(self):
        """Traps don't fire recursively."""
        calls = []

        def exit_handler():
            calls.append('start')
            # Try to fire another trap from within this handler
            inner_result = env.traps.fire(TrapType.ERR)
            calls.append(f'inner={inner_result}')
            return 0

        # Use env.traps since handlers use it via run()
        env.traps.set(TrapType.EXIT, InProcessCallable(exit_handler))
        env.traps.set(TrapType.ERR, InProcessCallable(lambda: 42))

        env.traps.fire(TrapType.EXIT)

        # Inner call should return None due to reentrancy guard
        assert calls == ['start', 'inner=None']

    def test_list_format(self):
        """list returns bash-style trap listing."""
        handler = InProcessCallable(lambda: 0)

        env.traps.set(TrapType.EXIT, handler)
        output = env.traps.list()

        assert 'trap --' in output
        assert 'EXIT' in output

    def test_copy_for_subshell_excludes_exit(self):
        """Subshell copy doesn't inherit EXIT trap."""
        manager = TrapManager()
        manager.set(TrapType.EXIT, InProcessCallable(lambda: 0))
        manager.set(TrapType.SIGINT, InProcessCallable(lambda: 0))

        copy = manager.copy_for_subshell()

        assert copy.get(TrapType.EXIT) is None
        assert copy.get(TrapType.SIGINT) is not None

    def test_copy_for_subshell_excludes_synthetic(self):
        """Subshell copy doesn't inherit DEBUG/ERR/RETURN/TRACE."""
        manager = TrapManager()
        manager.set(TrapType.DEBUG, InProcessCallable(lambda: 0))
        manager.set(TrapType.ERR, InProcessCallable(lambda: 0))
        manager.set(TrapType.TRACE, InProcessCallable(lambda: 0))
        manager.set(TrapType.RETURN, InProcessCallable(lambda: 0))

        copy = manager.copy_for_subshell()

        assert copy.get(TrapType.DEBUG) is None
        assert copy.get(TrapType.ERR) is None
        assert copy.get(TrapType.TRACE) is None
        assert copy.get(TrapType.RETURN) is None

    def test_copy_for_subshell_inherits_signals(self):
        """Subshell copy inherits signal traps."""
        manager = TrapManager()
        handler = InProcessCallable(lambda: 0)
        manager.set(TrapType.SIGTERM, handler)

        copy = manager.copy_for_subshell()

        assert copy.get(TrapType.SIGTERM) is handler

    def test_reset_for_child_clears_synthetic(self):
        """reset_for_child clears all synthetic traps."""
        manager = TrapManager()
        manager.set(TrapType.EXIT, InProcessCallable(lambda: 0))
        manager.set(TrapType.DEBUG, InProcessCallable(lambda: 0))
        manager.set(TrapType.SIGINT, InProcessCallable(lambda: 0))

        manager.reset_for_child()

        assert manager.get(TrapType.EXIT) is None
        assert manager.get(TrapType.DEBUG) is None
        assert manager.get(TrapType.SIGINT) is not None  # Signal kept

    def test_cleanup_clears_all(self):
        """cleanup clears all handlers and pending signals."""
        manager = TrapManager()
        manager.set(TrapType.EXIT, InProcessCallable(lambda: 0))
        manager.set(TrapType.DEBUG, InProcessCallable(lambda: 0))

        manager.cleanup()

        assert manager.get(TrapType.EXIT) is None
        assert manager.get(TrapType.DEBUG) is None


# =============================================================================
# Trap Helper Builtin Tests
# =============================================================================


class TestTrapListBuiltin:
    """Tests for the trap_list() builtin."""

    def test_trap_list_outputs_signal_names(self):
        """trap_list() prints available signal names."""
        result = capture(trap_list())
        output = result.read_stdout()

        # Should include both synthetic and signal traps
        assert 'DEBUG' in output
        assert 'EXIT' in output
        assert 'SIGINT' in output
        assert 'SIGTERM' in output


class TestTrapShowBuiltin:
    """Tests for the trap_show() builtin."""

    def test_trap_show_empty_when_no_traps(self):
        """trap_show() outputs nothing when no traps set."""
        result = capture(trap_show())
        assert result.read_stdout() == ''

    def test_trap_show_lists_set_traps(self):
        """trap_show() lists currently set traps."""
        env.traps.set(TrapType.EXIT, InProcessCallable(lambda: 0))

        result = capture(trap_show())
        output = result.read_stdout()

        assert 'EXIT' in output
        assert 'trap --' in output


# =============================================================================
# sys.exit() Handling Tests
# =============================================================================


class TestSystemExitHandling:
    """Tests for sys.exit() handling in subshells."""

    def test_subshell_catches_system_exit(self):
        """Subshell catches sys.exit() and exits with its code."""
        def raises_exit():
            sys.exit(42)

        subshell = Subshell(InProcessCallable(raises_exit))
        result = subshell()

        assert result.exit_code == 42

    def test_subshell_handles_exit_zero(self):
        """Subshell handles sys.exit(0) correctly."""
        def raises_exit():
            sys.exit(0)

        subshell = Subshell(InProcessCallable(raises_exit))
        result = subshell()

        assert result.exit_code == 0

    def test_subshell_handles_exit_none(self):
        """Subshell handles sys.exit(None) as exit code 0."""
        def raises_exit():
            sys.exit(None)

        subshell = Subshell(InProcessCallable(raises_exit))
        result = subshell()

        assert result.exit_code == 0


# =============================================================================
# Trap Integration Tests
# =============================================================================


class TestTrapIntegration:
    """Integration tests for trap firing with command execution."""

    def test_err_trap_fires_on_nonzero_exit(self):
        """ERR trap fires after command with non-zero exit."""
        errors = []
        env.traps.set(
            TrapType.ERR,
            InProcessCallable(lambda: errors.append(env.last_exit) or 0)
        )

        # Run a command that fails (InProcessCallable returning non-zero)
        InProcessCallable(lambda: 1)()

        assert errors == [1]

    def test_err_trap_does_not_fire_on_zero_exit(self):
        """ERR trap does not fire after successful command."""
        errors = []
        env.traps.set(
            TrapType.ERR,
            InProcessCallable(lambda: errors.append(True) or 0)
        )

        # Run a command that succeeds
        InProcessCallable(lambda: 0)()

        assert errors == []

    def test_debug_trap_fires_before_command(self):
        """DEBUG trap fires before command execution."""
        events = []

        def debug_handler():
            events.append('debug')
            return 0

        def cmd():
            events.append('cmd')
            return 0

        env.traps.set(TrapType.DEBUG, InProcessCallable(debug_handler))

        # Run a command - DEBUG should fire first
        InProcessCallable(cmd)()

        assert events == ['debug', 'cmd']

    def test_trace_trap_fires_after_command(self):
        """TRACE trap fires after every command."""
        events = []

        def trace_handler():
            events.append(f'trace:{env.last_exit}')
            return 0

        env.traps.set(TrapType.TRACE, InProcessCallable(trace_handler))

        # Run commands with different exit codes
        InProcessCallable(lambda: 0)()
        InProcessCallable(lambda: 1)()

        assert events == ['trace:0', 'trace:1']

    def test_multiple_traps_fire_in_order(self):
        """Multiple trap types fire in correct order."""
        events = []

        env.traps.set(TrapType.DEBUG, InProcessCallable(lambda: events.append('debug') or 0))
        env.traps.set(TrapType.TRACE, InProcessCallable(lambda: events.append('trace') or 0))
        env.traps.set(TrapType.ERR, InProcessCallable(lambda: events.append('err') or 0))

        # Run a failing command
        InProcessCallable(lambda: 1)()

        # Order: DEBUG (before), TRACE (after), ERR (after, on failure)
        assert events == ['debug', 'trace', 'err']


