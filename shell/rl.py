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

from __future__ import annotations

import ctypes
import errno
from collections.abc import Callable
from ctypes import CFUNCTYPE, c_char_p, c_int, c_void_p
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

# --- Key binding ---

# rl_parse_and_bind(const char *line) -> int
_rl.rl_parse_and_bind.argtypes = [c_char_p]
_rl.rl_parse_and_bind.restype = c_int

# --- Completion types and bindings ---

# Generator: char* func(const char* text, int state)
_COMPENTRY_FUNC = CFUNCTYPE(c_void_p, c_char_p, c_int)

# Attempted completion: char** func(const char* text, int start, int end)
_ATTEMPTED_COMPLETION_FUNC = CFUNCTYPE(c_void_p, c_char_p, c_int, c_int)

# rl_completion_matches(const char* text, compentry_func) -> char**
_rl.rl_completion_matches.argtypes = [c_char_p, _COMPENTRY_FUNC]
_rl.rl_completion_matches.restype = c_void_p

# rl_filename_completion_function(const char* text, int state) -> char*
_rl.rl_filename_completion_function.argtypes = [c_char_p, c_int]
_rl.rl_filename_completion_function.restype = c_void_p

# libc strdup — needed to return malloc'd strings from CFUNCTYPE callbacks
_libc = ctypes.CDLL(None)
_libc.strdup.argtypes = [c_char_p]
_libc.strdup.restype = c_void_p
_libc.free.argtypes = [c_void_p]
_libc.free.restype = None

# Reference to prevent unwanted GC
_active_callback: Callable[[str | None], None] | None = None
_active_completion_func: object | None = None  # _ATTEMPTED_COMPLETION_FUNC prevent GC
_active_generator: object | None = None  # _COMPENTRY_FUNC prevent GC
_word_break_bytes: bytes | None = None  # prevent GC of bytes pointed to by readline


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


# --- Completion API ---


def set_completer_delims(delims: str) -> None:
    """Set the word break characters used for completion."""
    global _word_break_bytes
    _word_break_bytes = delims.encode()
    c_char_p.in_dll(_rl, 'rl_completer_word_break_characters').value = _word_break_bytes


def get_completer_delims() -> str:
    """Get the current word break characters used for completion."""
    val = c_char_p.in_dll(_rl, 'rl_completer_word_break_characters').value
    return val.decode() if val else ''


def get_line_buffer() -> str:
    """Get the current contents of the readline input buffer."""
    val = c_char_p.in_dll(_rl, 'rl_line_buffer').value
    return val.decode() if val else ''


def set_completion_append_character(char: str) -> None:
    """Set the character appended after a completed match (default: space)."""
    c_int.in_dll(_rl, 'rl_completion_append_character').value = ord(char) if char else 0


def set_attempted_completion(
    func: Callable[[str, int, int], list[str] | None] | None,
) -> None:
    """Set the attempted completion function.

    func(text, start, end) receives:
    - text: the word being completed (extracted using word break chars)
    - start: byte offset of text in rl_line_buffer
    - end: byte offset of end of text in rl_line_buffer

    Return semantics:
    - None: fall through to readline's default filename completion
    - []: no matches, AND suppress default filename fallback
    - ['prefix', 'match1', ...]: explicit match list
    """
    global _active_completion_func, _active_generator

    if func is None:
        _active_completion_func = None
        c_void_p.in_dll(_rl, 'rl_attempted_completion_function').value = None
        return

    # Capture the Python matches for the generator to iterate over
    _matches: list[bytes] = []
    _match_idx = 0

    @_COMPENTRY_FUNC
    def generator(text: bytes | None, state: int) -> int | None:
        nonlocal _match_idx
        if state == 0:
            _match_idx = 0
        if _match_idx < len(_matches):
            result = _libc.strdup(_matches[_match_idx])
            _match_idx += 1
            return result
        return None

    @_ATTEMPTED_COMPLETION_FUNC
    def wrapper(text: bytes | None, start: int, end: int) -> int | None:
        nonlocal _matches
        text_str = text.decode() if text else ''
        py_result = func(text_str, start, end)

        if py_result is None:
            # Fall through to default filename completion
            return None

        if not py_result:
            # Empty list — suppress default fallback
            c_int.in_dll(_rl, 'rl_attempted_completion_over').value = 1
            return None

        # Build matches for the generator
        _matches = [m.encode() for m in py_result]
        return _rl.rl_completion_matches(text or b'', generator)

    _active_completion_func = wrapper
    _active_generator = generator
    c_void_p.in_dll(_rl, 'rl_attempted_completion_function').value = ctypes.cast(
        wrapper, c_void_p
    ).value


def set_tilde_expansion(enabled: bool) -> None:
    """Enable or disable tilde expansion during completion."""
    c_int.in_dll(_rl, 'rl_complete_with_tilde_expansion').value = 1 if enabled else 0


def parse_and_bind(command: str) -> None:
    """Execute a readline configuration command (same syntax as .inputrc)."""
    _rl.rl_parse_and_bind(command.encode())
