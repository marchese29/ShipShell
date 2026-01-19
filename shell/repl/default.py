"""
Default prompt-based REPL implementation using prompt_toolkit.

This module provides the standard interactive prompt interface.
Users can customize prompts and hooks via the global prompt_hooks and prompt_state.
"""

from __future__ import annotations

import codeop
import sys
from collections.abc import Callable

import jedi
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style
from pygments.lexers.python import Python3Lexer


def _detect_string_context(text: str) -> tuple[bool, str, int]:
    """
    Detect if cursor is inside a string literal (for path completion).

    Returns:
        (should_complete_paths, partial_path, start_offset)
        - should_complete_paths: True if we should offer path completions
        - partial_path: The path fragment typed so far
        - start_offset: How many chars back from cursor the path starts
    """
    if not text:
        return False, '', 0

    # Track state while parsing
    in_string = False
    string_char = None  # The quote character (' or ")
    is_fstring = False
    brace_depth = 0
    string_start = 0
    escaped = False

    i = 0
    while i < len(text):
        char = text[i]

        # Handle escape sequences
        if escaped:
            escaped = False
            i += 1
            continue

        if char == '\\' and in_string:
            escaped = True
            i += 1
            continue

        # Check for f-string prefix
        if not in_string and char in ('f', 'F'):
            if i + 1 < len(text) and text[i + 1] in ('"', "'"):
                is_fstring = True
                i += 1
                continue

        # Check for raw string prefix
        if not in_string and char in ('r', 'R'):
            if i + 1 < len(text) and text[i + 1] in ('"', "'"):
                i += 1
                continue

        # Handle quotes
        if char in ('"', "'"):
            if not in_string:
                # Starting a string
                in_string = True
                string_char = char
                string_start = i + 1
                brace_depth = 0
            elif char == string_char:
                # Ending the string (only if not in braces for f-strings)
                in_string = False
                string_char = None
                is_fstring = False
                brace_depth = 0

        # Handle f-string braces
        elif in_string and is_fstring:
            if char == '{':
                # Check if it's {{ (escaped brace)
                if i + 1 < len(text) and text[i + 1] == '{':
                    i += 1  # Skip the next brace
                else:
                    brace_depth += 1
            elif char == '}':
                # Check if it's }} (escaped brace)
                if i + 1 < len(text) and text[i + 1] == '}':
                    i += 1  # Skip the next brace
                else:
                    brace_depth = max(0, brace_depth - 1)

        i += 1

    # If we ended inside a string and not inside f-string braces, offer path completion
    if in_string and (not is_fstring or brace_depth == 0):
        # Extract the path fragment from string_start to end
        partial_path = text[string_start:]
        start_offset = len(text) - string_start
        return True, partial_path, start_offset

    return False, '', 0


class JediCompleter(Completer):
    """Custom completer using jedi for Python code completion with file path support."""

    def __init__(self, namespace: dict):
        """
        Initialize the JediCompleter.

        Args:
            namespace: The global namespace dictionary for the REPL.
        """
        self.namespace = namespace
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event):
        """
        Get completion suggestions from jedi and file paths.

        Args:
            document: The current document with text and cursor position.
            complete_event: The completion event.

        Yields:
            Completion objects for suggested completions.
        """
        # Get the text before the cursor
        text = document.text_before_cursor

        # Don't try to autocomplete before the user types something on the current line
        if text is None or not text.strip().split('\n')[-1].strip():
            return

        # Check if we're inside a string for path completion
        should_complete_paths, partial_path, start_offset = _detect_string_context(text)

        # Always try to get Jedi completions
        try:
            # Use jedi's Interpreter for REPL-style completion
            # This gives us access to the current namespace
            interpreter = jedi.Interpreter(text, namespaces=[self.namespace])

            # Get completions from jedi
            completions = interpreter.complete()

            # Convert jedi completions to prompt_toolkit Completions
            for completion in completions:
                # Use completion.complete which is just the suffix to add
                # (not the full name, so we don't replace what was already typed)
                complete_text = completion.complete
                if complete_text is not None:
                    yield Completion(
                        text=complete_text,
                        start_position=0,  # Insert at cursor, don't replace
                        display=completion.name,
                        display_meta=completion.type,
                    )
        except Exception:
            # Silently fail if jedi isn't available or has issues
            pass

        # If we're in a string, also provide path completions
        if should_complete_paths:
            try:
                # Create a modified document with just the path fragment
                path_document = Document(
                    text=partial_path,
                    cursor_position=len(partial_path),
                )

                # Get path completions
                for completion in self.path_completer.get_completions(
                    path_document, complete_event
                ):
                    # Adjust the completion to work with our actual document
                    # The PathCompleter gives us start_position relative to the path fragment
                    # We need to adjust it to be relative to our actual cursor position
                    yield Completion(
                        text=completion.text,
                        start_position=completion.start_position,
                        display=completion.display,
                        display_meta='path',
                    )
            except Exception:
                # Silently fail if path completion has issues
                pass


class PromptHooks:
    """Manages prompt display lifecycle hooks."""

    def __init__(self):
        self.before_prompt: list[Callable[[], None]] = []
        self.before_continuation: list[Callable[[str, str], None]] = []

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

    def _fire_hooks(self, hook_list: list[Callable], *args):
        """Fire all hooks in a list with consistent error handling."""
        for hook in hook_list:
            try:
                hook(*args)
            except Exception as e:
                print(f'Error in prompt hook: {e}', file=sys.stderr)

    def fire_before_prompt(self):
        """Fire all before_prompt hooks."""
        self._fire_hooks(self.before_prompt)

    def fire_before_continuation(self, prev_prompt: str, buffer: str):
        """Fire all before_continuation hooks."""
        self._fire_hooks(self.before_continuation, prev_prompt, buffer)


class PromptState:
    """Stores prompt configuration."""

    def __init__(self):
        self.primary_prompt = 'ship> '
        self.continuation_prompt = '..... '
        self.right_prompt = ''


class MultilineState:
    """Tracks multi-line mode state."""

    def __init__(self):
        self.enabled = False
        self.just_toggled = False  # Track if we just toggled mode


# Global instances
prompt_hooks = PromptHooks()
prompt_state = PromptState()
multiline_state = MultilineState()


def prompt():
    """Generator that yields complete code strings from interactive prompts.

    This is the default REPL input implementation. It uses prompt_toolkit to
    display interactive prompts and yields complete code blocks when ready.

    Supports multi-line mode (Ctrl-M) where enter inserts newlines and Ctrl-Enter submits.

    Yields:
        str: Complete code strings ready for execution.
    """
    # Show initial message
    print('ShipShell Python REPL')
    print("Type 'exit()' or press Ctrl+D to quit")
    print('Press Ctrl-X for multi-line mode')
    print()

    # Get the main module's namespace for completion
    import __main__

    repl_globals = __main__.__dict__

    # Create jedi-based completer with access to REPL namespace
    completer = JediCompleter(repl_globals)

    # Create custom key bindings
    bindings = KeyBindings()

    @bindings.add('c-x')  # Ctrl-X
    def _(event):
        """Toggle multi-line mode on/off with Ctrl-X."""
        if multiline_state.enabled:
            # Exiting multi-line mode - clear buffer and return to normal
            event.current_buffer.reset()
            multiline_state.enabled = False
        else:
            # Entering multi-line mode - preserve buffer and previous prompt line
            current_text = event.current_buffer.text
            multiline_state.enabled = True
            # Don't clear screen - keep previous prompt visible for context
            event.current_buffer.text = current_text

        # Mark that we just toggled - don't execute the buffer
        multiline_state.just_toggled = True

        # Restart the prompt to apply the new frame/toolbar settings
        event.app.exit(result=event.current_buffer.document.text)

    @bindings.add('c-s')  # Ctrl-S
    def _(event):
        """Submit code in multi-line mode with Ctrl-S."""
        if multiline_state.enabled:
            event.current_buffer.validate_and_handle()

    @bindings.add('enter')
    def _(event):
        """Handle Enter key - newline in multi-line mode, submit otherwise."""
        if multiline_state.enabled:
            # In multi-line mode, Enter just inserts a newline
            event.current_buffer.insert_text('\n')
        else:
            # In normal mode, Enter validates and submits (default behavior)
            event.current_buffer.validate_and_handle()

    def get_bottom_toolbar():
        """Return toolbar text showing current mode."""
        if multiline_state.enabled:
            return HTML(
                '<style bg="ansiblack" fg="ansiyellow"> MULTI-LINE MODE </style> '
                '<style bg="ansiblack">Ctrl-S to submit, Ctrl-X to exit</style>'
            )
        else:
            return HTML(
                '<style fg="ansigreen" bg="ansiblack"> NORMAL MODE </style> '
                '<style bg="ansiblack">Ctrl-X for multi-line mode</style>'
            )

    # Style for the frame border in multi-line mode
    frame_style = Style.from_dict(
        {
            'frame.border': '#888888',
        }
    )

    # Create prompt session with autocomplete enabled
    # Note: complete_in_thread=False to avoid issues with forking in subshells
    session = PromptSession(
        completer=completer,
        complete_while_typing=True,
        complete_in_thread=False,
        lexer=PygmentsLexer(Python3Lexer),
        key_bindings=bindings,
        bottom_toolbar=get_bottom_toolbar,
    )

    buffer = ''
    prev_prompt = prompt_state.primary_prompt

    # Statement completeness checker
    compiler = codeop.CommandCompiler()

    while True:
        try:
            # === GET PROMPT CONFIGURATION ===
            if multiline_state.enabled:
                # Multi-line mode configuration
                prompt_text = ''
                rprompt_text = ''
                style = frame_style
                show_frame = True
                multiline = True
            else:
                # Normal mode configuration
                is_continuation = bool(buffer)
                if is_continuation:
                    prompt_hooks.fire_before_continuation(prev_prompt, buffer)
                    prompt_text = prompt_state.continuation_prompt
                else:
                    prompt_hooks.fire_before_prompt()
                    prev_prompt = prompt_state.primary_prompt
                    prompt_text = prompt_state.primary_prompt

                rprompt_text = prompt_state.right_prompt
                style = None
                show_frame = False
                multiline = False

            # === READ INPUT ===
            line = session.prompt(
                ANSI(prompt_text),
                rprompt=ANSI(rprompt_text) if rprompt_text else None,
                style=style,
                show_frame=show_frame,
                multiline=multiline,
                default=buffer if buffer else '',  # Pre-fill with existing buffer
            )

            # === UPDATE BUFFER ===
            # When toggling with Ctrl-X, the buffer is managed by the toggle function
            # Here we just handle normal input accumulation
            if buffer and line and not line.startswith(buffer):
                # Normal continuation - append to buffer
                buffer += '\n' + line
            else:
                # First line, or full buffer from Ctrl-X toggle
                buffer = line

            # === EXECUTE BASED ON MODE ===
            if multiline_state.enabled:
                # MULTI-LINE MODE: User controls submission with Ctrl-S
                # Check if we just toggled - if so, don't execute
                if multiline_state.just_toggled:
                    multiline_state.just_toggled = False
                    continue  # Don't execute, just continue to next prompt

                # No completeness checking - just execute what they submitted
                yield buffer
                buffer = ''
            else:
                # NORMAL MODE: Check if statement is complete before executing
                try:
                    code_obj = compiler(buffer)
                except SyntaxError as e:
                    # Syntax error - display and clear buffer
                    print(f'SyntaxError: {e}')
                    buffer = ''
                    continue

                if code_obj is None:
                    # Incomplete statement - continue getting input
                    continue

                # Complete statement - execute it
                yield buffer
                buffer = ''

        except KeyboardInterrupt:
            # Ctrl+C - cancel current input
            print('^C')
            buffer = ''
        except EOFError:
            # Ctrl+D - exit
            return
