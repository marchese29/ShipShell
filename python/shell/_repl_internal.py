from __future__ import annotations

import codeop
import traceback
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI

if TYPE_CHECKING:
    from .repl import REPLHooks, REPLState


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
    while True:
        # Default implementation
        print("ShipShell Python REPL")
        print("Type 'exit()' or press Ctrl+D to quit")
        print()

        session = PromptSession()
        buffer = ""
        prev_prompt = state.primary_prompt

        # Statement completeness checker
        compiler = codeop.CommandCompiler()

        # Get the main module's namespace for REPL execution
        import __main__

        repl_globals = __main__.__dict__

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
                    ANSI(f"\x1b[0m{prompt_text}"),
                    rprompt=ANSI(f"\x1b[0m{rprompt_text}") if rprompt_text else None,
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
                        break

                # Clear buffer for next statement
                buffer = ""

            except KeyboardInterrupt:
                # Ctrl+C - cancel current input
                print("^C")
                buffer = ""
            except EOFError:
                # Ctrl+D - exit
                print("Exiting...")
                break
