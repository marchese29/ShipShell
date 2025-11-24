from __future__ import annotations

import codeop
import traceback
from typing import TYPE_CHECKING

import jedi
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.python import Python3Lexer

if TYPE_CHECKING:
    from .repl import REPLHooks, REPLState


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
        return False, "", 0

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

        if char == "\\" and in_string:
            escaped = True
            i += 1
            continue

        # Check for f-string prefix
        if not in_string and char in ("f", "F"):
            if i + 1 < len(text) and text[i + 1] in ('"', "'"):
                is_fstring = True
                i += 1
                continue

        # Check for raw string prefix
        if not in_string and char in ("r", "R"):
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
            if char == "{":
                # Check if it's {{ (escaped brace)
                if i + 1 < len(text) and text[i + 1] == "{":
                    i += 1  # Skip the next brace
                else:
                    brace_depth += 1
            elif char == "}":
                # Check if it's }} (escaped brace)
                if i + 1 < len(text) and text[i + 1] == "}":
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

    return False, "", 0


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
                        display_meta="path",
                    )
            except Exception:
                # Silently fail if path completion has issues
                pass


def _is_expression(code: str) -> bool:
    """Check if code is a valid expression (not a statement)."""
    try:
        compile(code, "<string>", "eval")
        return True
    except SyntaxError:
        return False


def _execute_code(hooks: REPLHooks, code: str, globals_dict: dict) -> None:
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


def loop(hooks: REPLHooks, state: REPLState):
    # Default implementation
    print("ShipShell Python REPL")
    print("Type 'exit()' or press Ctrl+D to quit")
    print()

    # Get the main module's namespace for REPL execution
    import __main__

    repl_globals = __main__.__dict__

    # Create jedi-based completer with access to REPL namespace
    completer = JediCompleter(repl_globals)

    # Create prompt session with autocomplete enabled
    session = PromptSession(
        completer=completer,
        complete_while_typing=True,  # Show completions as you type
        complete_in_thread=True,
        lexer=PygmentsLexer(Python3Lexer),
    )
    buffer = ""
    prev_prompt = state.primary_prompt

    # Statement completeness checker
    compiler = codeop.CommandCompiler()

    while True:
        try:
            # Determine if we're in continuation mode
            is_continuation = bool(buffer)

            # Fire appropriate hooks
            if is_continuation:
                hooks.fire_before_continuation(prev_prompt, buffer)
                prompt_text = state.continuation_prompt
            else:
                hooks.fire_before_prompt()
                prev_prompt = state.primary_prompt
                prompt_text = state.primary_prompt

            # Get right prompt
            rprompt_text = state.right_prompt

            # Read line from user
            line = session.prompt(
                ANSI(prompt_text),
                rprompt=ANSI(rprompt_text) if rprompt_text else None,
            )

            # Append to buffer
            if buffer:
                buffer += "\n"
            buffer += line

            # Check if statement is complete
            code_obj = compiler(buffer)

            if code_obj is None:
                # Incomplete - need more input
                continue

            # Statement is complete - execute it
            if buffer.strip():
                try:
                    _execute_code(hooks, buffer, repl_globals)
                except SystemExit:
                    print("Exiting...")
                    return

            # Clear buffer for next statement
            buffer = ""

        except KeyboardInterrupt:
            # Ctrl+C - cancel current input
            print("^C")
            buffer = ""
        except EOFError:
            # Ctrl+D - exit
            print("Exiting...")
            return
