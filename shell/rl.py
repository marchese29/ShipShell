"""
Python bindings for GNU readline's callback interface.

This module provides an event-loop-friendly alternative to blocking readline calls.
Instead of blocking on input, you register a callback and call read_char() whenever
stdin is readable (e.g., from a select loop).

Example usage:
    def on_line(line):
        print(f'Got: {line}')

    install_line_handler('>>> ', on_line)
    while True:
        readable, _, _ = select.select([sys.stdin], [], [])
        if sys.stdin in readable:
            read_char()

Note: This requires GNU readline, not libedit (macOS's default). On macOS,
install via Homebrew: `brew install readline`
"""

import ctypes
import errno
from collections.abc import Callable
from ctypes import CFUNCTYPE, c_char_p
from ctypes.util import find_library

REQUIRED_SYMBOLS = [
    'rl_callback_handler_install',
    'rl_callback_read_char',
    'rl_callback_sigcleanup',
    'rl_callback_handler_remove',
]


def _load_readline() -> ctypes.CDLL:
    candidates = [
        # Explicit GNU readline paths (Homebrew)
        '/opt/homebrew/opt/readline/lib/libreadline.dylib',  # macOS Apple Silicon (Homebrew)
        '/usr/local/opt/readline/lib/libreadline.dylib',  # macOS Intel (Homebrew)
        '/opt/homebrew/lib/libreadline.dylib',  # macOS Apple Silicon (linked)
        '/usr/local/lib/libreadline.dylib',  # macOS Intel (linked)
        # Let the linker figure it out
        'libreadline.so.8',
        'libreadline.so',
        find_library('readline'),
    ]

    for path in candidates:
        if path is None:
            continue
        try:
            lib = ctypes.CDLL(path)
            if all(hasattr(lib, sym) for sym in REQUIRED_SYMBOLS):
                return lib
        except OSError:
            continue

    raise OSError('Could not find GNU readline with callback support')


_rl = _load_readline()

# Callback Type: void (*rl_linefunc)(char *line)
_LINEFUNC = CFUNCTYPE(None, c_char_p)

# rl_callback_handler_install(const char *prompt, rl_vcpfunc_t *lhandler)
_rl.rl_callback_handler_install.argtypes = [c_char_p, _LINEFUNC]
_rl.rl_callback_handler_install.restype = None

# rl_callback_read_char(void)
_rl.rl_callback_read_char.argtypes = []
_rl.rl_callback_read_char.restype = None

# rl_callback_sigcleanup(void)
_rl.rl_callback_sigcleanup.argtypes = []
_rl.rl_callback_sigcleanup.restype = None

# rl_callback_handler_remove(void)
_rl.rl_callback_handler_remove.argtypes = []
_rl.rl_callback_handler_remove.restype = None

# add_history(const char *line)
_rl.add_history.argtypes = [c_char_p]
_rl.add_history.restype = None

# read_history(const char *filename) -> int
_rl.read_history.argtypes = [c_char_p]
_rl.read_history.restype = ctypes.c_int

# write_history(const char *filename) -> int
_rl.write_history.argtypes = [c_char_p]
_rl.write_history.restype = ctypes.c_int

# Reference to prevent unwanted GC
_active_callback: Callable[[str | None], None] | None = None


def install_line_handler(
    prompt: str,
    callback: Callable[[str | None], None],
    eof_callback: Callable[[], None] | None = None,
):
    """Install a readline callback handler with the given prompt.

    Args:
        prompt: The prompt string to display.
        callback: Called with the line text when user presses Enter.
        eof_callback: Called when user sends EOF (Ctrl-D on empty line).

    The handler remains active until remove_handler() is called. While active,
    call read_char() whenever stdin has data available.
    """
    global _active_callback

    @_LINEFUNC
    def wrapper(line: bytes | None):
        if line:
            callback(line.decode())
        elif eof_callback:
            eof_callback()

    _active_callback = wrapper
    _rl.rl_callback_handler_install(prompt.encode(), wrapper)


def read_char():
    """Process one character of input.

    Call this when stdin is readable (e.g., after select()). When a complete
    line is ready, the callback registered with install_line_handler() fires.
    """
    _rl.rl_callback_read_char()


def sigcleanup():
    """Clean up readline state after a signal interruption.

    Call this after catching SIGINT (Ctrl-C) to reset readline's internal state.
    """
    _rl.rl_callback_sigcleanup()


def remove_handler():
    """Remove the current callback handler and restore terminal state.

    Always call this before printing output or executing code, as readline
    puts the terminal in a special editing mode.
    """
    global _active_callback
    _rl.rl_callback_handler_remove()
    _active_callback = None


def add_history(line: str):
    """Add a line to the history list."""
    _rl.add_history(line.encode())


def read_history(path: str) -> None:
    """Load history from a file.

    Silently succeeds if the file doesn't exist (ENOENT).
    Raises OSError for other failures (permission denied, etc.).
    """
    result = _rl.read_history(path.encode())
    if result != 0 and result != errno.ENOENT:
        raise OSError(result, f'Failed to read history: {path}')


def write_history(path: str) -> None:
    """Save history to a file.

    Raises OSError on failure.
    """
    result = _rl.write_history(path.encode())
    if result != 0:
        raise OSError(result, f'Failed to write history: {path}')
