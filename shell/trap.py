"""Shell trap/signal handling system.

This module provides the core trap infrastructure supporting:
- Synthetic traps: DEBUG, TRACE, ERR, EXIT, RETURN
- Real signal traps: SIGINT, SIGTERM, SIGHUP, etc.

Trap handlers are stored as ShellRunnable instances for type safety and composability.
Signal handling uses a queue-based approach for safety.
"""

from __future__ import annotations

import signal
from collections import deque
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .model import ShellRunnable


class TrapType(Enum):
    """Types of traps supported by the shell.

    Synthetic traps (shell events):
        DEBUG   - Fires BEFORE command execution
        TRACE   - Fires AFTER every command (regardless of exit code)
        ERR     - Fires AFTER command with non-zero exit only
        EXIT    - Fires when shell exits
        RETURN  - Fires when function returns (bash compat)

    Signal traps (OS signals):
        SIGINT, SIGTERM, SIGHUP, SIGQUIT, SIGALRM, SIGUSR1, SIGUSR2
    """

    # Synthetic traps: use negative values to distinguish from signal numbers
    # (Enum requires unique values; None would deduplicate all synthetics)
    DEBUG = -1
    TRACE = -2
    ERR = -3
    EXIT = -4
    RETURN = -5

    # Signal traps: value is the signal number
    SIGINT = signal.SIGINT
    SIGTERM = signal.SIGTERM
    SIGHUP = signal.SIGHUP
    SIGQUIT = signal.SIGQUIT
    SIGALRM = signal.SIGALRM
    SIGUSR1 = signal.SIGUSR1
    SIGUSR2 = signal.SIGUSR2

    # Class-level name aliases for parsing (populated below)
    _name_aliases: ClassVar[dict[str, TrapType]]

    @property
    def signal_number(self) -> int | None:
        """Return the signal number for signal traps, None for synthetic traps."""
        return self.value if self.value > 0 else None

    @property
    def is_signal(self) -> bool:
        """True if this is a real OS signal trap."""
        return self.value > 0

    @property
    def is_synthetic(self) -> bool:
        """True if this is a synthetic (shell event) trap."""
        return self.value < 0

    @classmethod
    def from_string(cls, name: str) -> TrapType:
        """Parse a trap name string to TrapType.

        Accepts:
            - Synthetic names: DEBUG, TRACE, ERR, EXIT, RETURN, 0 (for EXIT)
            - Signal names: INT, SIGINT, TERM, SIGTERM, etc.
            - Case insensitive

        Raises:
            ValueError: If the name is not recognized
        """
        upper_name = name.upper()
        if upper_name in cls._name_aliases:
            return cls._name_aliases[upper_name]
        raise ValueError(f'Unknown trap: {name}')


# Build name aliases mapping after enum is defined
TrapType._name_aliases = {
    # Synthetic - by name
    'DEBUG': TrapType.DEBUG,
    'TRACE': TrapType.TRACE,
    'ERR': TrapType.ERR,
    'EXIT': TrapType.EXIT,
    'RETURN': TrapType.RETURN,
    # EXIT can also be specified as '0' (bash compat)
    '0': TrapType.EXIT,
    # Signals - with and without SIG prefix
    'INT': TrapType.SIGINT,
    'SIGINT': TrapType.SIGINT,
    'TERM': TrapType.SIGTERM,
    'SIGTERM': TrapType.SIGTERM,
    'HUP': TrapType.SIGHUP,
    'SIGHUP': TrapType.SIGHUP,
    'QUIT': TrapType.SIGQUIT,
    'SIGQUIT': TrapType.SIGQUIT,
    'ALRM': TrapType.SIGALRM,
    'SIGALRM': TrapType.SIGALRM,
    'USR1': TrapType.SIGUSR1,
    'SIGUSR1': TrapType.SIGUSR1,
    'USR2': TrapType.SIGUSR2,
    'SIGUSR2': TrapType.SIGUSR2,
}


class TrapManager:
    """Manages trap handlers for the shell.

    Handlers are stored as ShellRunnable instances. Callers are responsible for
    wrapping callables in InProcessCallable before passing to set_trap.

    Signal traps use queue-based handling for safety:
    - Signal handlers only queue the signal
    - Processing happens at safe checkpoints (in run())
    """

    def __init__(self):
        # Trap handlers: TrapType -> ShellRunnable
        self._handlers: dict[TrapType, ShellRunnable] = {}

        # Queue of pending signals to process
        self._pending_signals: deque[TrapType] = deque()

        # Saved original Python signal handlers (for restoration)
        self._original_handlers: dict[int, Any] = {}

        # Re-entrancy guard: prevent traps firing during trap execution
        self._in_trap_handler: bool = False

    def set(
        self,
        trap_type: TrapType,
        handler: ShellRunnable | Callable[[], Any] | None,
    ) -> None:
        """Set a trap handler.

        Args:
            trap_type: The trap to set
            handler: ShellRunnable, plain callable, or None to clear.
                     Plain callables are auto-wrapped in InProcessCallable.

        Examples:
            env.traps.set(TrapType.EXIT, lambda: print("goodbye"))
            env.traps.set(TrapType.ERR, my_error_handler)
            env.traps.set(TrapType.EXIT, None)  # Clear trap
        """
        if handler is None:
            self._clear_trap(trap_type)
            return

        # Auto-wrap plain callables
        # Import here to avoid circular import: trap.py ← environment.py ← model.py
        from .model import InProcessCallable, ShellRunnable

        if not isinstance(handler, ShellRunnable):
            handler = InProcessCallable(handler)

        self._handlers[trap_type] = handler
        self._install_signal_handler(trap_type)

    def _clear_trap(self, trap_type: TrapType) -> None:
        """Clear a trap and restore default signal handling."""
        if trap_type in self._handlers:
            del self._handlers[trap_type]

        # Restore original signal handler if it was a signal trap
        if trap_type.is_signal:
            sig_num = trap_type.signal_number
            assert sig_num is not None  # Guaranteed by is_signal check
            if sig_num in self._original_handlers:
                signal.signal(sig_num, self._original_handlers[sig_num])
                del self._original_handlers[sig_num]

    def _install_signal_handler(self, trap_type: TrapType) -> None:
        """Install signal handler for signal traps (queues signal for later processing)."""
        if not trap_type.is_signal:
            return  # Not a signal trap

        sig_num = trap_type.signal_number
        assert sig_num is not None  # Guaranteed by is_signal check

        # Install queue-based handler, saving the original
        def queue_signal(signum: int, frame: Any) -> None:
            self._pending_signals.append(trap_type)

        old_handler = signal.signal(sig_num, queue_signal)
        if sig_num not in self._original_handlers:
            self._original_handlers[sig_num] = old_handler

    def get(self, trap_type: TrapType) -> ShellRunnable | None:
        """Get the current handler for a trap (or None if not set)."""
        return self._handlers.get(trap_type)

    def fire(self, trap_type: TrapType) -> int | None:
        """Execute a trap handler if set.

        The re-entrancy guard prevents recursive trap firing. If this is called
        while already in a trap handler, it returns None immediately. This allows
        trap handlers to safely call run() on other runnables.

        Args:
            trap_type: The trap to fire

        Returns:
            Exit code from handler, or None if no handler set or in re-entrant call
        """
        if self._in_trap_handler:
            return None

        handler = self._handlers.get(trap_type)
        if handler is None:
            return None

        self._in_trap_handler = True
        try:
            # Execute via __call__ -> run() for proper fork handling
            result = handler()
            return result.exit_code
        finally:
            self._in_trap_handler = False

    def process_pending_signals(self) -> None:
        """Process any pending signals from the queue.

        Called at safe checkpoints (in run()) to handle queued signals.
        """
        while self._pending_signals:
            trap_type = self._pending_signals.popleft()
            self.fire(trap_type)

    def list(self) -> str:
        """Format trap list for display (trap -p style output)."""
        lines = []
        for trap_type, handler in self._handlers.items():
            name = trap_type.name
            handler_str = repr(handler)
            lines.append(f'trap -- {handler_str} {name}')
        return '\n'.join(lines)

    def copy_for_subshell(self) -> TrapManager:
        """Create a copy of trap state for a subshell.

        Handles semantic differences in trap inheritance:
        - EXIT: Never inherited (each subshell has its own)
        - DEBUG/ERR/RETURN/TRACE: Not inherited (unless functrace/errtrace - future)
        - Signal traps: Inherited (fork already copies signal handlers)
        """
        new_manager = TrapManager()

        for trap_type, handler in self._handlers.items():
            # Skip EXIT - never inherited
            if trap_type == TrapType.EXIT:
                continue
            # Skip synthetic traps - require shell options for inheritance (future)
            if trap_type in (TrapType.DEBUG, TrapType.ERR, TrapType.RETURN, TrapType.TRACE):
                continue

            # Signal traps are inherited
            new_manager._handlers[trap_type] = handler

        return new_manager

    def reset_for_child(self) -> None:
        """Reset trap state for a forked child (subshell, pipeline stage).

        Clears synthetic traps (EXIT, DEBUG, ERR, TRACE, RETURN) in place.
        Keeps signal traps since they're inherited via fork anyway.

        This mutates the current manager rather than creating a copy,
        which is simpler to use in forked children where we already have
        the manager from the parent process.
        """
        for trap_type in list(self._handlers.keys()):
            if trap_type.is_synthetic:
                del self._handlers[trap_type]

    def cleanup(self) -> None:
        """Restore all original signal handlers."""
        for sig_num, original_handler in self._original_handlers.items():
            try:
                signal.signal(sig_num, original_handler)
            except (ValueError, OSError):
                pass
        self._original_handlers.clear()
        self._handlers.clear()
        self._pending_signals.clear()
