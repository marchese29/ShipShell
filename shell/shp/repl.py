"""
ShipShell REPL API - Stubs for IDE support.

These stubs provide type hints and minimal implementations for use
outside of the ShipShell environment. In actual ShipShell, these are
replaced by Rust-native implementations.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Callable

__all__ = [
    "REPLHook",
    "set_prompt",
    "get_prompt",
    "set_continuation",
    "get_continuation",
    "set_right_prompt",
    "get_right_prompt",
    "on",
    "off",
    "list_hooks",
    "print_hooks",
]


class REPLHook(IntEnum):
    """Enumeration of available REPL hook points.

    Hooks allow you to register callbacks that are called at specific
    points in the REPL lifecycle.
    """

    BeforePrompt = 0
    """Called before rendering the primary prompt."""

    BeforeContinuation = 1
    """Called before rendering a continuation prompt.
    
    Args to callback:
        prev_prompt (str): The previous prompt that was shown
        buffer (str): The current incomplete statement buffer
    """

    BeforeExecute = 2
    """Called before executing a statement.
    
    Args to callback:
        command (str): The complete statement about to be executed
    """

    AfterExecute = 3
    """Called after executing a statement.
    
    Args to callback:
        command (str): The statement that was just executed
    """


def set_prompt(value: str) -> None:
    """Set the primary prompt string.

    Args:
        value: The new prompt string to display.

    Examples:
        set_prompt(">>> ")
        set_prompt("🚀 ")
    """
    raise NotImplementedError("set_prompt() only works in ShipShell REPL")


def get_prompt() -> str:
    """Get the current primary prompt string.

    Returns:
        The current primary prompt string.

    Examples:
        current = get_prompt()
        print(f"Current prompt: {current}")
    """
    raise NotImplementedError("get_prompt() only works in ShipShell REPL")


def set_continuation(value: str) -> None:
    """Set the continuation prompt string.

    The continuation prompt is shown when entering multi-line statements.

    Args:
        value: The new continuation prompt string to display.

    Examples:
        set_continuation("... ")
        set_continuation("    ")
    """
    raise NotImplementedError("set_continuation() only works in ShipShell REPL")


def get_continuation() -> str:
    """Get the current continuation prompt string.

    Returns:
        The current continuation prompt string.

    Examples:
        current = get_continuation()
        print(f"Continuation prompt: {current}")
    """
    raise NotImplementedError("get_continuation() only works in ShipShell REPL")


def set_right_prompt(value: str) -> None:
    """Set the right prompt string.

    The right prompt is displayed on the right side of the terminal.

    Args:
        value: The new right prompt string to display.

    Examples:
        set_right_prompt("[python]")
        set_right_prompt("🐍")

        # Can be used with hooks to dynamically update
        import datetime
        def update_time():
            now = datetime.datetime.now().strftime("%H:%M:%S")
            set_right_prompt(f"[{now}]")
        on(REPLHook.BeforePrompt, update_time)
    """
    raise NotImplementedError("set_right_prompt() only works in ShipShell REPL")


def get_right_prompt() -> str:
    """Get the current right prompt string.

    Returns:
        The current right prompt string.

    Examples:
        current = get_right_prompt()
        print(f"Right prompt: {current}")
    """
    raise NotImplementedError("get_right_prompt() only works in ShipShell REPL")


def on(hook: REPLHook, callback: Callable) -> int:
    """Register a callback for a REPL hook.

    The callback will be invoked at the specified hook point. Each hook type
    expects a specific callback signature (see REPLHook documentation).

    Args:
        hook: The hook point to register the callback for.
        callback: The function to call when the hook fires.

    Returns:
        A unique ID for this hook registration, which can be used to
        unregister the hook later with off().

    Examples:
        # Simple hook with no arguments
        id1 = on(REPLHook.BeforePrompt, lambda: print("About to show prompt"))

        # Hook with arguments
        id2 = on(REPLHook.BeforeExecute, lambda cmd: print(f"Executing: {cmd}"))

        # Continuation hook
        def on_continuation(prev_prompt, buffer):
            print(f"Continuing from {prev_prompt}, buffer: {buffer}")
        id3 = on(REPLHook.BeforeContinuation, on_continuation)
    """
    raise NotImplementedError("on() only works in ShipShell REPL")


def off(hook: REPLHook, id: int) -> bool:
    """Unregister a callback from a REPL hook by ID.

    Args:
        hook: The hook type to unregister from.
        id: The unique ID returned by on() when the hook was registered.

    Returns:
        True if the hook was found and removed, False if not found.

    Examples:
        # Register a hook
        hook_id = on(REPLHook.BeforePrompt, lambda: print("Hello"))

        # Later, unregister it
        removed = off(REPLHook.BeforePrompt, hook_id)
        if removed:
            print("Hook removed successfully")
        else:
            print("Hook not found")
    """
    raise NotImplementedError("off() only works in ShipShell REPL")


def list_hooks(hook: REPLHook) -> list[int]:
    """List all registered hook IDs for a specific hook type.

    Returns the IDs in registration order.

    Args:
        hook: The hook type to list IDs for.

    Returns:
        A list of hook IDs registered for the specified hook type.

    Examples:
        # Register some hooks
        id1 = on(REPLHook.BeforePrompt, lambda: print("First"))
        id2 = on(REPLHook.BeforePrompt, lambda: print("Second"))

        # List them
        ids = list_hooks(REPLHook.BeforePrompt)
        print(f"Registered hooks: {ids}")  # [1, 2]
    """
    raise NotImplementedError("list_hooks() only works in ShipShell REPL")


def print_hooks() -> None:
    """Print all registered hooks grouped by type.

    Displays each hook type with its registered IDs in order. Useful for
    debugging and inspecting the current hook state.

    Examples:
        # Register some hooks
        on(REPLHook.BeforePrompt, lambda: None)
        on(REPLHook.BeforeExecute, lambda cmd: None)

        # Print summary
        print_hooks()
        # Output:
        # Registered REPL Hooks:
        #   BeforePrompt: [1]
        #   BeforeContinuation: []
        #   BeforeExecute: [1]
        #   AfterExecute: []
    """
    raise NotImplementedError("print_hooks() only works in ShipShell REPL")
