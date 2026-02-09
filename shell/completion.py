"""Tab completion for ShipShell REPL.

Provides filename/directory completion inside string literals.
Outside strings, completion is suppressed (future: Python identifier completion).
"""

from __future__ import annotations

from shell import rl

# Default word break characters for completion — includes quotes so that
# inside cd('/tmp/fo<TAB>, the text to complete is /tmp/fo (not cd('/tmp/fo).
_WORD_BREAK_CHARS = ' \t\n"\'`@$><=;|&{('


def _in_string_literal(line: str, pos: int) -> bool:
    """Check if position pos in line falls inside a string literal.

    Scans line[0:pos] for unmatched quotes, handling:
    - Single and double quotes
    - Triple-quoted strings (\"\"\" and ''')
    - Escaped quotes inside strings (\\' and \\")
    """
    quote_char = None
    triple = False
    i = 0
    while i < pos:
        c = line[i]
        if c == '\\' and i + 1 < pos and quote_char is not None:
            i += 2  # skip escaped character inside string
            continue
        if quote_char is None:
            if c in ('"', "'") and line[i : i + 3] == c * 3:
                quote_char = c
                triple = True
                i += 3
            elif c in ('"', "'"):
                quote_char = c
                triple = False
                i += 1
            else:
                i += 1
        elif triple and line[i : i + 3] == quote_char * 3:
            quote_char = None
            triple = False
            i += 3
        elif not triple and c == quote_char:
            quote_char = None
            i += 1
        else:
            i += 1
    return quote_char is not None


def _complete(text: str, start: int, end: int) -> list[str] | None:
    """Attempted completion function for readline.

    Inside a string literal: return None to let readline fall back to its
    built-in filename completer.
    Outside a string: return [] to suppress all completion.
    """
    line = rl.get_line_buffer()

    if _in_string_literal(line, start):
        rl.set_completion_append_character('')  # don't append anything after match
        return None  # fall through to filename completion

    return []  # suppress completion outside strings


def setup() -> None:
    """Configure readline completion for ShipShell."""
    rl.set_completer_delims(_WORD_BREAK_CHARS)
    rl.set_attempted_completion(_complete)
    rl.parse_and_bind(r'TAB: menu-complete')
    rl.parse_and_bind(r'"\e[Z": menu-complete-backward')  # Shift-Tab cycles backward
