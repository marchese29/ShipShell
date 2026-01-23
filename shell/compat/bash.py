from __future__ import annotations

import fnmatch
import glob
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import tree_sitter as ts
import tree_sitter_bash as tsbash
from tree_sitter import Language, Parser

from shell.environment import ShellEnvironment, env as global_env
from shell.model import InProcessCallable, NotPipeline, Pipeline, ShellRunnable, capture, prog


def _expect[T](thing: T | None) -> T:
    assert thing is not None
    return thing


BashValue = str | int | list[str]


@dataclass
class CallFrame:
    """Represents a function call's execution context."""

    func_name: str
    positional_params: list[str]
    local_vars: dict[str, BashValue] = field(default_factory=dict)


class BashReturnException(Exception):
    """Raised when a return statement is executed inside a function."""

    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code
        super().__init__(f'return {exit_code}')


def _bash_to_str(value: BashValue | None) -> str:
    match value:
        case int() as val:
            return str(val)
        case str() as val:
            return val
        case list() as val:
            if len(val) == 0:
                return ''
            return val[0]
        case None:
            return ''


def _bash_to_file(value: BashValue | None) -> int | str:
    """Convert BashValue to a file target (fd int or path str)."""
    match value:
        case int() as val:
            return val  # File descriptor
        case str() as val:
            return val  # File path
        case list() as val:
            return val[0] if val else ''
        case None:
            return ''


def _bash_to_int(value: BashValue | None) -> int:
    match value:
        case int() as val:
            return val
        case str() as val:
            try:
                return int(val)
            except Exception:
                return 0
        case list() as val:
            return 0
        case None:
            return 0


def _expand_range(content: str) -> list[str] | None:
    if '..' not in content:
        return None

    parts = content.split('..')
    if len(parts) < 2 or len(parts) > 3:
        return None

    start = parts[0].strip()
    end = parts[1].strip()
    inc = parts[2].strip() if len(parts) == 3 else None

    try:
        increment = int(inc) if inc else 1
        if increment == 0:  # Only reject zero increment
            return None
    except ValueError:
        return None

    # Try with numbers
    try:
        start_num = int(start)
        end_num = int(end)

        pad_width = 0
        if len(start) > 1 and start[0] == '0':
            pad_width = len(start)
        if len(end) > 1 and end[0] == '0':
            pad_width = max(pad_width, len(end))

        if start_num <= end_num:
            return [
                str(n).zfill(pad_width)
                for n in range(start_num, end_num + 1, abs(increment))
            ]
        else:
            return [
                str(n).zfill(pad_width)
                for n in range(start_num, end_num - 1, -abs(increment))
            ]
    except ValueError:
        pass


def _split_commas(string: str) -> list[str]:
    result: list[str] = []
    current = ''
    depth = 0
    i = 0

    while i < len(string):
        char = string[i]

        # Deal with escaped characters
        if (
            char == '\\'
            and i + 1 < len(string)
            and string[i + 1] in ('{', '}', ',', '\\')
        ):
            current += char + string[i + 1]
            i += 2
            continue

        # Unescaped characters
        if char == '{':
            depth += 1
            current += char
        elif char == '}':
            depth -= 1
            current += char
        elif depth == 0 and char == ',':
            result.append(current)
            current = ''
        else:
            current += char

        # Continue iterating
        i += 1

    if len(current) > 0:
        result.append(current)

    return result


def _expand_braces(string: str) -> list[str]:
    result: list[str] = ['']
    i = 0
    while i < len(string):
        char = string[i]

        # Deal with escaped characters
        if char == '\\' and i + 1 < len(string):
            next_char = string[i + 1]
            if next_char in ('{', '}', ',', '\\'):
                result = [r + next_char for r in result]
                i += 2
                continue
            else:
                result = [r + char for r in result]
                i += 1
                continue

        if char == '{':
            depth = 1
            j = i + 1

            # Find the closing bracket
            while j < len(string) and depth > 0:
                # Deal with escaped characters
                if (
                    string[j] == '\\'
                    and j + 1 < len(string)
                    and string[j + 1] in ('{', '}', '\\')
                ):
                    j += 2
                    continue

                if string[j] == '{':
                    depth += 1
                elif string[j] == '}':
                    depth -= 1
                j += 1

            if depth == 0:
                content = string[i + 1 : j - 1]

                # First see if the braces are a range
                range_result = _expand_range(content)
                if range_result is not None:
                    # Range:
                    # Use the result as the expanded options
                    expanded_options = range_result
                else:
                    # Not a range:
                    # Get options by separating on commas at the current depth
                    expanded_options = []
                    for option in _split_commas(content):
                        # Flatten if there are multiple options
                        expanded_options.extend(_expand_braces(option))

                # Cartesian Product
                new_result = []
                for prefix in result:
                    for option in expanded_options:
                        new_result.append(prefix + option)
                result = new_result
                i = j
            else:
                # Unclosed brackets are treated like normal text
                result = [r + char for r in result]
                i += 1
        else:
            # Everything else is appended to the results we have so far
            result = [r + char for r in result]
            i += 1
    return result if len(result) > 1 or result[0] != '' else []


class BashScriptError(Exception):
    """Raised when bash script execution should terminate with an error."""

    def __init__(self, message: str, exit_code: int = 1):
        self.exit_code = exit_code
        super().__init__(message)


class SyntheticNode:
    """A programmatically constructed AST node for expression restructuring.

    Used to fix tree-sitter-bash's incorrect operator precedence in test
    expressions. Implements the minimal interface that evaluate_* methods need.
    """

    __slots__ = ('type', '_fields', '_children')

    def __init__(self, node_type: str, fields: dict | None = None):
        self.type = node_type
        self._fields = fields or {}
        # Build children list from fields for iteration
        self._children: list = []
        for v in self._fields.values():
            if isinstance(v, list):
                self._children.extend(v)
            elif v is not None:
                self._children.append(v)

    def child_by_field_name(self, name: str):
        return self._fields.get(name)

    def children_by_field_name(self, name: str) -> list:
        val = self._fields.get(name)
        if val is None:
            return []
        return val if isinstance(val, list) else [val]

    @property
    def children(self) -> list:
        return self._children

    @property
    def child_count(self) -> int:
        return len(self._children)

    @property
    def is_named(self) -> bool:
        return True

    @property
    def named_children(self) -> list:
        return self._children


class BashCSTVisitor:
    # Statements
    def visit(self, node: ts.Node):
        method_name = f'visit_{node.type}'
        method: Callable[[ts.Node], None] | None = getattr(self, method_name, None)

        if method is not None:
            method(node)
        else:
            return self.visit_children(node)

    def visit_children(self, node: ts.Node):
        children_to_visit = (
            [n for n in node.children if n.is_named] if node.child_count > 0 else []
        )
        for child in children_to_visit:
            self.visit(child)

    # Expressions
    def evaluate(self, node: ts.Node | SyntheticNode) -> BashValue:
        method_name = f'evaluate_{node.type}'
        method = getattr(self, method_name, None)

        if method is not None:
            return method(node)
        elif isinstance(node, ts.Node):
            return self.evaluate_children(node)
        else:
            raise ValueError(f'No handler for {type(node).__name__} with type: {node.type}')

    def evaluate_children(self, node: ts.Node) -> BashValue:
        parts: list[BashValue] = []
        for child in node.named_children:
            parts.append(self.evaluate(child))
        return ''.join([_bash_to_str(p) for p in parts])


# Mapping of short flags to shell option names for `set` command
FLAG_TO_OPTION = {
    'a': 'allexport',
    'b': 'notify',
    'e': 'errexit',
    'f': 'noglob',
    'h': 'hashall',
    'k': 'keyword',
    'm': 'monitor',
    'n': 'noexec',
    'p': 'privileged',
    't': 'onecmd',
    'u': 'nounset',
    'v': 'verbose',
    'x': 'xtrace',
    'B': 'braceexpand',
    'C': 'noclobber',
    'E': 'errtrace',
    'H': 'histexpand',
    'P': 'physical',
    'T': 'functrace',
}


class ShipBashInterpreter(BashCSTVisitor):
    def __init__(
        self,
        source: str,
        args: list[str] | None = None,
        script_name: str = 'bash',
        env: ShellEnvironment = global_env,
    ):
        self._source = source
        self._positional_params = args if args is not None else []
        self._script_name = script_name
        self._env = env
        self._temp_files: list[str] = []  # Temp files to clean up (e.g., heredocs)
        self._resolve_vars = False  # When True, variable_name nodes are looked up

        # Shell options (set -o). All are parsed; behavior implemented incrementally.
        self._shell_options: dict[str, bool] = {
            'errexit': False,
            'nounset': False,
            'xtrace': False,
            'pipefail': False,
            'noglob': False,
            'allexport': False,
            'verbose': False,
            'noexec': False,
            'braceexpand': True,    # ON by default
            'noclobber': False,
            'errtrace': False,
            'functrace': False,
            'physical': False,
            'hashall': True,        # ON by default
            'keyword': False,
            'onecmd': False,
            'privileged': False,
            'histexpand': False,
            'notify': False,
            'monitor': False,
            'emacs': False,
            'vi': False,
            'history': False,
            'ignoreeof': False,
            'interactive-comments': True,  # ON by default
            'posix': False,
            'nolog': False,
        }

        # True when inside a context where errexit shouldn't trigger (conditions, && chains)
        self._errexit_suppressed = False

        # Function definitions: name -> compound_statement body node
        self._functions: dict[str, ts.Node] = {}

        # Call stack for function scope management
        self._call_stack: list[CallFrame] = []

        # Track cleanup state for __del__
        self._cleaned_up = False

    def cleanup(self):
        """Clean up temporary resources created during execution."""
        for path in self._temp_files:
            try:
                os.unlink(path)
            except OSError:
                pass  # File may already be deleted
        self._temp_files.clear()
        self._cleaned_up = True

    def __del__(self):
        """Destructor - cleanup when garbage collected (for wired functions)."""
        if not getattr(self, '_cleaned_up', False):
            self.cleanup()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup is called."""
        self.cleanup()
        return False  # Don't suppress exceptions

    def _execute_function_body(
        self, func_name: str, args: list[str], body_node: ts.Node
    ) -> int:
        """Execute a function body with the given arguments.

        This is the shared implementation for both direct function calls
        (from _build_function_runnable) and Python-wired function calls.

        Args:
            func_name: Name of the function being called
            args: Positional arguments passed to the function
            body_node: The compound_statement AST node containing the function body

        Returns:
            The exit code from the function execution
        """
        frame = CallFrame(func_name=func_name, positional_params=args)
        self._call_stack.append(frame)
        try:
            self.visit_children(body_node)
        except BashReturnException as e:
            self._env.last_exit = e.exit_code
        finally:
            self._call_stack.pop()
        return self._env.last_exit

    # --- Shell option helpers ---

    @contextmanager
    def _suppress_errexit(self):
        """Context manager to suppress errexit (for conditions, && chains, etc.)."""
        was_suppressed = self._errexit_suppressed
        self._errexit_suppressed = True
        try:
            yield
        finally:
            self._errexit_suppressed = was_suppressed

    @contextmanager
    def _arithmetic_context(self):
        """Context manager for arithmetic evaluation where bare words are variables."""
        old_resolve_vars = self._resolve_vars
        self._resolve_vars = True
        try:
            yield
        finally:
            self._resolve_vars = old_resolve_vars

    def _check_errexit(self):
        """Raise BashScriptError if errexit conditions met."""
        if (
            self._shell_options['errexit']
            and not self._errexit_suppressed
            and self._env.last_exit != 0
        ):
            raise BashScriptError(
                f'set -e: command exited with status {self._env.last_exit}',
                self._env.last_exit,
            )

    def _check_nounset(self, var_name: str) -> None:
        """Raise BashScriptError if nounset enabled and variable is unset.

        Special variables ($?, $#, $$, $!, $@, $*) are always available.

        Note: Prefer using _get_variable(check_unset=True) which includes this check.
        """
        # Special variables are always defined - skip nounset check
        if var_name in ('?', '#', '$', '!', '@', '*', '-', '_'):
            return
        # Check local scopes first
        for frame in reversed(self._call_stack):
            if var_name in frame.local_vars:
                return
        if self._shell_options['nounset'] and var_name not in self._env:
            raise BashScriptError(f'{var_name}: unbound variable', 127)

    def _get_positional_params(self) -> list[str]:
        """Get positional params from current call frame or script level."""
        if self._call_stack:
            return self._call_stack[-1].positional_params
        return self._positional_params

    def _get_variable(self, name: str, *, check_unset: bool = True) -> BashValue | None:
        """Get variable with dynamic scoping. Returns None if unset.

        Resolution order (first match wins):
        1. Local variables in call stack (most recent frame first)
        2. Environment variables

        Args:
            name: Variable name to look up
            check_unset: If True (default), raises BashScriptError when nounset
                         is enabled and variable is unset. Set to False when the
                         caller handles unset explicitly (e.g., ${var-default}).

        Returns:
            Variable value, or None if unset.

        Raises:
            BashScriptError: If nounset enabled, check_unset=True, and var is unset.
        """
        # Check local scopes first (dynamic scoping)
        for frame in reversed(self._call_stack):
            if name in frame.local_vars:
                return frame.local_vars[name]

        # Check environment
        if name in self._env:
            return self._env[name]

        # Variable is unset - check nounset if requested
        if check_unset and self._shell_options['nounset']:
            # Special variables are always defined
            if name not in ('?', '#', '$', '!', '@', '*', '-', '_'):
                raise BashScriptError(f'{name}: unbound variable', 127)

        return None

    def _set_variable(self, name: str, value: BashValue) -> None:
        """Set variable respecting local scope.

        If variable is declared local in any active frame, set it there.
        Otherwise, set in environment (global).
        """
        for frame in reversed(self._call_stack):
            if name in frame.local_vars:
                frame.local_vars[name] = value
                return
        # Global scope
        self._env[name] = value if isinstance(value, list) else _bash_to_str(value)

    def _build_shellopts(self) -> str:
        """Build SHELLOPTS string for subshell inheritance."""
        return ':'.join(name for name, enabled in self._shell_options.items() if enabled)

    def visit(self, node: ts.Node):
        """Override to handle verbose, noexec, and errexit."""
        # Verbose (-v): Print input lines as they are read (before expansion)
        # Only print for "statement-level" nodes, not sub-expressions
        if self._shell_options['verbose'] and node.type in (
            'command', 'pipeline', 'list', 'compound_statement',
            'if_statement', 'while_statement', 'for_statement',
            'case_statement', 'function_definition',
        ):
            print(self._get_text(node), file=sys.stderr)

        # Noexec (-n): Read commands but don't execute (syntax check mode)
        if self._shell_options['noexec']:
            # Still visit children to parse/validate, but skip actual execution
            # by not calling super().visit() which triggers visit_* methods
            for child in node.children:
                if child.is_named:
                    self.visit(child)
            return

        super().visit(node)
        # Don't check errexit for 'list' nodes - they contain && or || chains
        # which shouldn't trigger errexit. Individual commands inside are
        # handled separately with suppression.
        if node.type != 'list':
            self._check_errexit()

    def _get_text(self, node: ts.Node) -> str:
        return self._source[node.start_byte : node.end_byte]

    def _glob_pattern_to_regex(
        self,
        pattern: str,
        anchor_start: bool = False,
        anchor_end: bool = False,
        greedy: bool = True,
    ) -> re.Pattern[str]:
        """Convert bash glob pattern to compiled regex pattern.

        Args:
            pattern: The glob pattern to convert
            anchor_start: If True, anchor pattern to start with ^
            anchor_end: If True, anchor pattern to end with $
            greedy: If True, use greedy matching (*); if False, use non-greedy (*?)

        Returns:
            A compiled regex pattern
        """
        # Use fnmatch.translate to convert glob to regex
        # fnmatch.translate returns a regex with (?s:...) wrapper and \Z at the end
        regex_pattern = fnmatch.translate(pattern)

        # Remove the (?s:...) wrapper and \Z suffix that fnmatch adds
        # fnmatch.translate returns something like: '(?s:pattern)\\Z'
        if regex_pattern.startswith('(?s:') and regex_pattern.endswith(')\\Z'):
            regex_pattern = regex_pattern[4:-3]  # Remove (?s: and )\Z

        # If non-greedy, replace .* with .*?
        if not greedy:
            regex_pattern = regex_pattern.replace('.*', '.*?')

        # Add anchors as needed
        if anchor_start:
            regex_pattern = '^' + regex_pattern
        if anchor_end:
            regex_pattern = regex_pattern + '$'

        # Compile and return the pattern
        return re.compile(regex_pattern)

    def _apply_string_expansion(
        self,
        value_str: str,
        operator: str,
        arg1_str: str | None,
        arg2_str: str | None,
    ) -> str:
        """Apply a per-element expansion operator to a single string value.

        This handles operators that should be applied element-by-element when
        the parameter is @ or * or an array subscript.

        Args:
            value_str: The string value to transform
            operator: The expansion operator (e.g., "#", "##", "^", etc.)
            arg1_str: First argument as string (pattern, offset, etc.)
            arg2_str: Second argument as string (replacement, length, etc.)

        Returns:
            The transformed string
        """
        match operator:
            case ':':
                # Substring expansion: ${var:offset} or ${var:offset:length}
                offset = _bash_to_int(arg1_str)

                # Handle negative offset (count from end)
                if offset < 0:
                    offset = len(value_str) + offset
                    if offset < 0:
                        offset = 0

                # If offset is beyond string length, return empty
                if offset >= len(value_str):
                    return ''

                if arg2_str is not None:
                    length = _bash_to_int(arg2_str)
                    if length < 0:
                        return ''
                    return value_str[offset : offset + length]
                else:
                    return value_str[offset:]

            case '#':
                # Remove shortest prefix match
                if arg1_str is None:
                    return value_str
                regex = self._glob_pattern_to_regex(
                    arg1_str, anchor_start=True, greedy=False
                )
                match = regex.match(value_str)
                if match:
                    return value_str[match.end() :]
                return value_str

            case '##':
                # Remove longest prefix match
                if arg1_str is None:
                    return value_str
                regex = self._glob_pattern_to_regex(
                    arg1_str, anchor_start=True, greedy=True
                )
                match = regex.match(value_str)
                if match:
                    return value_str[match.end() :]
                return value_str

            case '%':
                # Remove shortest suffix match - search from right to find rightmost match
                if arg1_str is None:
                    return value_str
                regex = self._glob_pattern_to_regex(arg1_str, anchor_end=True)
                for i in range(len(value_str) - 1, -1, -1):
                    if regex.match(value_str[i:]):
                        return value_str[:i]
                return value_str

            case r'%%':
                # Remove longest suffix match
                if arg1_str is None:
                    return value_str
                regex = self._glob_pattern_to_regex(
                    arg1_str, anchor_end=True, greedy=True
                )
                match = regex.search(value_str)
                if match:
                    return value_str[: match.start()]
                return value_str

            case '/':
                # Replace first match
                if arg1_str is None:
                    return value_str
                replacement = arg2_str if arg2_str is not None else ''
                regex = self._glob_pattern_to_regex(arg1_str)
                return regex.sub(replacement, value_str, count=1)

            case '//':
                # Replace all matches
                if arg1_str is None:
                    return value_str
                replacement = arg2_str if arg2_str is not None else ''
                regex = self._glob_pattern_to_regex(arg1_str)
                return regex.sub(replacement, value_str)

            case '/#':
                # Replace if matches at start
                if arg1_str is None:
                    return value_str
                replacement = arg2_str if arg2_str is not None else ''
                regex = self._glob_pattern_to_regex(arg1_str, anchor_start=True)
                return regex.sub(replacement, value_str, count=1)

            case '/%':
                # Replace if matches at end
                if arg1_str is None:
                    return value_str
                replacement = arg2_str if arg2_str is not None else ''
                regex = self._glob_pattern_to_regex(arg1_str, anchor_end=True)
                return regex.sub(replacement, value_str, count=1)

            case '^':
                # Uppercase first character (optionally matching pattern)
                if not value_str:
                    return ''

                if arg1_str:
                    pattern_regex = self._glob_pattern_to_regex(arg1_str)
                    result = ''
                    found_first = False
                    for char in value_str:
                        if not found_first and pattern_regex.match(char):
                            result += char.upper()
                            found_first = True
                        else:
                            result += char
                    return result
                else:
                    return value_str[0].upper() + value_str[1:]

            case '^^':
                # Uppercase all characters (optionally matching pattern)
                if not value_str:
                    return ''

                if arg1_str:
                    pattern_regex = self._glob_pattern_to_regex(arg1_str)
                    result = ''
                    for char in value_str:
                        if pattern_regex.match(char):
                            result += char.upper()
                        else:
                            result += char
                    return result
                else:
                    return value_str.upper()

            case ',':
                # Lowercase first character (optionally matching pattern)
                if not value_str:
                    return ''

                if arg1_str:
                    pattern_regex = self._glob_pattern_to_regex(arg1_str)
                    result = ''
                    found_first = False
                    for char in value_str:
                        if not found_first and pattern_regex.match(char):
                            result += char.lower()
                            found_first = True
                        else:
                            result += char
                    return result
                else:
                    return value_str[0].lower() + value_str[1:]

            case ',,':
                # Lowercase all characters (optionally matching pattern)
                if not value_str:
                    return ''

                if arg1_str:
                    pattern_regex = self._glob_pattern_to_regex(arg1_str)
                    result = ''
                    for char in value_str:
                        if pattern_regex.match(char):
                            result += char.lower()
                        else:
                            result += char
                    return result
                else:
                    return value_str.lower()

            # Note: Case toggle operators (~ and ~~) are not supported because
            # tree-sitter-bash doesn't recognize the syntax, and macOS system
            # bash doesn't support them either.

            case '@':
                # Transformation operators
                match arg1_str:
                    case 'U':
                        return value_str.upper()
                    case 'u':
                        if len(value_str) == 0:
                            return value_str
                        elif len(value_str) == 1:
                            return value_str.upper()
                        else:
                            return f'{value_str[0].upper()}{value_str[1:]}'
                    case 'L':
                        return value_str.lower()
                    case 'Q' | 'E':
                        raise NotImplementedError('Quoted expansions')
                    case 'P':
                        raise NotImplementedError('Prompt String expansion')
                    case 'A':
                        raise NotImplementedError('Declaration recreation expansion')
                    case 'K' | 'k':
                        raise NotImplementedError('Array printing expansion')
                    case 'a':
                        raise NotImplementedError('Parameter attribute expansion')
                return value_str

            case _:
                return value_str

    def evaluate_ansi_c_string(self, node: ts.Node) -> BashValue:
        """Evaluate ANSI-C quoted strings: $'...'

        Supports escape sequences like \\n, \\t, \\xHH, \\NNN, etc.
        """
        text = self._get_text(node)
        # Strip $' prefix and ' suffix
        if text.startswith("$'") and text.endswith("'"):
            content = text[2:-1]
        else:
            return text

        result = []
        i = 0
        while i < len(content):
            if content[i] == '\\' and i + 1 < len(content):
                next_char = content[i + 1]
                match next_char:
                    case 'a':
                        result.append('\a')
                        i += 2
                    case 'b':
                        result.append('\b')
                        i += 2
                    case 'e' | 'E':
                        result.append('\x1b')  # Escape
                        i += 2
                    case 'f':
                        result.append('\f')
                        i += 2
                    case 'n':
                        result.append('\n')
                        i += 2
                    case 'r':
                        result.append('\r')
                        i += 2
                    case 't':
                        result.append('\t')
                        i += 2
                    case 'v':
                        result.append('\v')
                        i += 2
                    case '\\':
                        result.append('\\')
                        i += 2
                    case "'":
                        result.append("'")
                        i += 2
                    case '"':
                        result.append('"')
                        i += 2
                    case '?':
                        result.append('?')
                        i += 2
                    case 'x':
                        # Hex escape: \xHH
                        hex_chars = content[i + 2 : i + 4]
                        if len(hex_chars) >= 1 and all(
                            c in '0123456789abcdefABCDEF' for c in hex_chars
                        ):
                            result.append(chr(int(hex_chars, 16)))
                            i += 2 + len(hex_chars)
                        else:
                            result.append('\\x')
                            i += 2
                    case c if c in '0123456789':
                        # Octal escape: \NNN (1-3 digits)
                        octal_chars = ''
                        j = i + 1
                        while j < len(content) and j < i + 4 and content[j] in '01234567':
                            octal_chars += content[j]
                            j += 1
                        if octal_chars:
                            result.append(chr(int(octal_chars, 8) % 256))
                            i = j
                        else:
                            result.append(content[i])
                            i += 1
                    case _:
                        # Unknown escape - keep the backslash
                        result.append('\\')
                        i += 1
            else:
                result.append(content[i])
                i += 1

        return ''.join(result)

    def evaluate_arithmetic_expansion(self, node: ts.Node) -> BashValue:
        with self._arithmetic_context():
            parts = []
            for child in node.named_children:
                parts.append(self.evaluate(child))
            if len(parts) == 0:
                return 0
            elif len(parts) == 1:
                return parts[0]
            else:
                return ''.join([_bash_to_str(p) for p in parts])

    def evaluate_brace_expression(self, node: ts.Node) -> BashValue:
        result = _expand_range(self._get_text(node)[1:-1])
        return result if result else []

    def evaluate_command_substitution(self, node: ts.Node) -> BashValue:
        """Handle command substitution: $(command) or `command`."""
        if node.child_by_field_name('redirect') is not None:
            raise NotImplementedError('redirect in command substitution not supported')

        # TODO: Handle multi-command children

        # Find the command node (skip the $( and ) tokens)
        command_node = next(
            (
                c
                for c in node.children
                if c.is_named and c.type != 'command_substitution'
            ),
            None,
        )

        if command_node is None:
            # Empty command substitution
            return ''

        # Build a runnable from the command
        return capture(self._node_to_runnable(command_node)).read_stdout()

    def evaluate_concatenation(self, node: ts.Node) -> BashValue:
        """Concatenate child nodes, expanding variables and other constructs."""
        parts: list[str] = []
        for child in node.children:
            # Handle anonymous '$' node with text '$$' (PID) - tree-sitter quirk
            # where $$ in concatenations isn't wrapped in simple_expansion
            if child.type == '$' and self._get_text(child) == '$$':
                value = self._env.get('$', '')
            else:
                value = self.evaluate(child)
            parts.append(_bash_to_str(value))
        result = ''.join(parts)
        # Apply brace expansion to the final result
        expanded = _expand_braces(result)
        return expanded if len(expanded) > 1 else expanded[0] if expanded else ''

    def evaluate_expansion(self, node: ts.Node) -> BashValue:
        """Handle ${var} and other complex expansions including ${10}, ${11}, etc."""

        # We're finally bitten by dealing with a CST instead of an AST.  It's easier here
        # to do a pseudo-AST run than trying to pick things out one-by-one
        prefix_operator: str | None = None
        value_node: ts.Node | None = None
        operator: str | None = None
        arg1: ts.Node | None = None
        arg2: ts.Node | None = None

        node_iterator = iter(node.children)
        next(node_iterator)  # Consume the "${"

        next_node = next(node_iterator)
        text = self._get_text(next_node)
        if text in ('!', '#'):
            prefix_operator = text
            next_node = next(node_iterator)
        value_node = next_node

        next_node = next(node_iterator)
        text = self._get_text(next_node)
        if not next_node.is_named and text != '}':
            operator = text
            next_node = next(node_iterator)
            text = self._get_text(next_node)
            # Only set arg1 if this isn't the closing brace
            if text != '}':
                arg1 = next_node
                next_node = next(node_iterator)
                text = self._get_text(next_node)
        if not next_node.is_named and text != '}':
            # next_node is the argument separator (like '/' or ':')
            # Get the next node to see if there's an arg2
            next_node = next(node_iterator)
            if next_node.is_named:
                arg2 = next_node
            # else: it's '}', meaning arg2 is empty (e.g., ${x/pattern/})

        value: BashValue | None = None
        if prefix_operator == '!':
            if operator is None:
                # ${!varname} - indirect variable reference
                var_text = self._get_text(value_node)
                indirect = _bash_to_str(self._get_variable(var_text, check_unset=False))
                indirect_value = self._get_variable(indirect, check_unset=False)
                return _bash_to_str(indirect_value)
            elif operator in ('@', '*'):
                # ${!prefix@} and ${!prefix*}
                prefix = self._get_text(value_node)
                variables = [k for k in self._env.keys() if k.startswith(prefix)]
                if operator == '@':
                    return variables
                else:
                    return ' '.join(variables)
            else:
                # ${!name[@]} and ${!name[*]}
                var_name = self._get_text(value_node)
                var_value = self._get_variable(var_name, check_unset=False)

                # TODO: Support for more complex types like sparse/associative arrays
                if var_value is None or not isinstance(var_value, list):
                    return [] if arg1 and self._get_text(arg1) == '@' else ''

                indices = [str(i) for i in range(len(var_value))]
                if arg1 and self._get_text(arg1) == '@':
                    return indices
                else:
                    return ' '.join(indices)
        elif value_node.type in ('variable_name', 'special_variable_name'):
            var_name = self._get_text(value_node)
            if var_name == '0':
                value = self._script_name
            elif var_name.isnumeric():
                idx = int(var_name) - 1
                params = self._get_positional_params()
                if 0 <= idx < len(params):
                    value = params[idx]
                else:
                    value = ''
            elif var_name in ('@', '*'):
                value = self._get_positional_params()
            else:
                # Operators that handle unset explicitly should skip nounset check
                check_unset = operator not in ('-', ':-', '=', ':=', '+', ':+', '?', ':?')
                value = self._get_variable(var_name, check_unset=check_unset)
        else:
            # Subscript
            value = self.evaluate(value_node)

        if prefix_operator == '#':
            if isinstance(value, list):
                return len(value)
            else:
                return len(_bash_to_str(value))

        # Return the value as-is if no operators
        if operator is None:
            # Preserve lists (arrays with @ or * subscript)
            if isinstance(value, list):
                return value
            return _bash_to_str(value)

        match operator:
            case '-':
                if value is None:
                    return self._get_text(_expect(arg1))
                else:
                    return value
            case ':-':
                if value is None or value == '':
                    return self._get_text(_expect(arg1))
                else:
                    return value
            case '=':
                # Assign if variable is unset (None)
                if value is None:
                    var_name = self._get_text(value_node)
                    new_value = _bash_to_str(self.evaluate(_expect(arg1)))
                    self._env[var_name] = new_value
                    return new_value
                return _bash_to_str(value)
            case ':=':
                # Assign if variable is unset OR empty
                if value is None or value == '':
                    var_name = self._get_text(value_node)
                    new_value = _bash_to_str(self.evaluate(_expect(arg1)))
                    self._env[var_name] = new_value
                    return new_value
                return _bash_to_str(value)
            case '?':
                # Error if variable is unset
                if value is None:
                    var_name = self._get_text(value_node)
                    error_msg = (
                        self._get_text(_expect(arg1))
                        if arg1
                        else 'parameter null or not set'
                    )
                    raise BashScriptError(f'{var_name}: {error_msg}')
                return _bash_to_str(value)
            case ':?':
                # Error if variable is unset OR empty
                if value is None or value == '':
                    var_name = self._get_text(value_node)
                    error_msg = (
                        self._get_text(_expect(arg1))
                        if arg1
                        else 'parameter null or not set'
                    )
                    raise BashScriptError(f'{var_name}: {error_msg}')
                return _bash_to_str(value)
            case '+':
                if value is None:
                    return ''
                else:
                    return self._get_text(_expect(arg1))
            case ':+':
                if value is None or value == '':
                    return ''
                else:
                    return self._get_text(_expect(arg1))
            case _:
                # Per-element operators: apply to each element if value is a list
                # Pre-evaluate arguments to strings
                arg1_str: str | None = None
                arg2_str: str | None = None

                if arg1:
                    # For substring (:), evaluate arg1 as expression; otherwise get text
                    if operator == ':':
                        arg1_str = _bash_to_str(self.evaluate(arg1))
                    else:
                        arg1_str = self._get_text(arg1)

                if arg2:
                    if operator == ':':
                        arg2_str = _bash_to_str(self.evaluate(arg2))
                    else:
                        arg2_str = self._get_text(arg2)

                # Apply operator to each element if list, otherwise to single value
                if isinstance(value, list):
                    return [
                        self._apply_string_expansion(elem, operator, arg1_str, arg2_str)
                        for elem in value
                    ]
                else:
                    return self._apply_string_expansion(
                        _bash_to_str(value), operator, arg1_str, arg2_str
                    )

    def evaluate_array(self, node: ts.Node) -> BashValue:
        result: list[str] = []
        for child in node.named_children:
            result.append(_bash_to_str(self.evaluate(child)))
        return result

    def evaluate_subshell(self, node: ts.Node) -> BashValue:
        runnable = self._node_to_runnable(node)
        return capture(runnable).read_stdout()

    def evaluate_regex(self, node: ts.Node) -> BashValue:
        """Return the regex pattern as-is."""
        return self._get_text(node)

    def evaluate_subscript(self, node: ts.Node) -> BashValue:
        var_name_node = _expect(node.child_by_field_name('name'))
        index_node = _expect(node.child_by_field_name('index'))

        # Get the raw index text to check for special subscripts @ and *
        index_text = self._get_text(index_node)
        var_name = self._get_text(var_name_node)

        var_value = self._env.get(var_name)

        if var_value is None:
            self._check_nounset(var_name)
            return ''

        # Handle special subscripts @ and * for arrays
        if index_text in ('@', '*'):
            if isinstance(var_value, list):
                # @ and * both return all elements as a list
                # (The difference is in quoting context, handled elsewhere)
                return var_value
            else:
                # Scalar variable with @ or * subscript returns the value
                return var_value

        # Regular numeric index
        if not isinstance(var_value, list):
            # Scalar with numeric subscript - index 0 returns value, others empty
            index_val = _bash_to_int(self.evaluate(index_node))
            return var_value if index_val == 0 else ''

        index_val = _bash_to_int(self.evaluate(index_node))
        if isinstance(index_val, list):
            return ''
        if isinstance(index_val, str):
            try:
                index_val = int(index_val)
            except Exception:
                return ''

        if index_val < 0 or index_val >= len(var_value):
            # TODO: Throw an error?
            return ''
        return var_value[index_val]

    def evaluate_number(self, node: ts.Node) -> BashValue:
        """Evaluate numbers.  Bash only supports integers"""
        # Expression evaluating to a number
        if node.named_child_count > 0:
            child = _expect(node.named_child(0))
            return _bash_to_int(self.evaluate(child))

        # Text as number
        try:
            return int(self._get_text(node))
        except Exception:
            return 0

    def evaluate_process_substitution(self, node: ts.Node) -> BashValue:
        # TODO: grep <(cmd -arg)
        # This will probably need to be something that adds to an accrued state
        raise NotImplementedError('process_substitution expression')

    def evaluate_string(self, node: ts.Node) -> BashValue:
        """Double-quoted strings with variable/command expansion.

        We need to track byte positions to capture literal text (including newlines)
        between named children, similar to heredoc handling.
        """
        result = []
        # Skip opening quote
        current_pos = node.start_byte + 1
        end_pos = node.end_byte - 1  # Before closing quote

        for child in node.children:
            if not child.is_named:
                continue
            # Capture literal text before this child
            if child.start_byte > current_pos:
                result.append(self._source[current_pos : child.start_byte])
            # Evaluate the child (handles expansions)
            # In string context, $@ and $* expand to all elements joined by space
            child_value = self.evaluate(child)
            if isinstance(child_value, list):
                result.append(' '.join(child_value))
            else:
                result.append(_bash_to_str(child_value))
            current_pos = child.end_byte

        # Capture any remaining literal text
        if end_pos > current_pos:
            result.append(self._source[current_pos:end_pos])

        return ''.join(result)

    def evaluate_raw_string(self, node: ts.Node) -> BashValue:
        """Single-quoted strings are not given an expansion"""
        return self._get_text(node)[1:-1]

    def evaluate_string_content(self, node: ts.Node) -> BashValue:
        """Content inside double-quoted strings"""
        return self._get_text(node)

    def evaluate_heredoc_body(self, node: ts.Node) -> BashValue:
        """Evaluate heredoc body with variable/command expansion.

        Tree-sitter-bash doesn't create child nodes for:
        - Text before the first expansion
        - Text-only heredocs (no children at all)
        So we track byte positions to capture these gaps.
        """
        result = []
        current_pos = node.start_byte

        for child in node.children:
            # Capture literal text before this child
            if child.start_byte > current_pos:
                result.append(self._source[current_pos:child.start_byte])

            # Evaluate the child
            # In heredoc context, $@ and $* expand to all elements joined by space
            child_value = self.evaluate(child)
            if isinstance(child_value, list):
                result.append(' '.join(child_value))
            else:
                result.append(_bash_to_str(child_value))
            current_pos = child.end_byte

        # Capture trailing literal text (or all text if no children)
        if node.end_byte > current_pos:
            result.append(self._source[current_pos:node.end_byte])

        return ''.join(result)

    def evaluate_heredoc_content(self, node: ts.Node) -> BashValue:
        """Literal content inside heredoc body"""
        return self._get_text(node)

    def evaluate_simple_expansion(self, node: ts.Node) -> BashValue:
        for child in node.named_children:
            if child.type in ('variable_name', 'special_variable_name'):
                var_name = self._get_text(child)
                if var_name == '0':
                    return self._script_name
                elif var_name == '#':
                    return len(self._get_positional_params())
                elif var_name.isnumeric():
                    idx = int(var_name) - 1
                    params = self._get_positional_params()
                    if 0 <= idx < len(params):
                        return params[idx]
                    else:
                        return ''
                elif var_name in ('@', '*'):
                    return self._get_positional_params()
                else:
                    # _get_variable handles nounset check
                    value = self._get_variable(var_name)
                    return value if value is not None else ''
        return ''

    def evaluate_word(self, node: ts.Node) -> BashValue:
        text = self._get_text(node)

        # In arithmetic context, bare words are variable references
        if self._resolve_vars and text.isidentifier():
            value = self._get_variable(text)
            return value if value is not None else ''

        # Expand tilde at the start of the word (unquoted words only)
        # os.path.expanduser handles ~, ~/path, ~username, and ~username/path
        if text.startswith('~'):
            text = os.path.expanduser(text)

        # Glob expansion (pathname expansion) for patterns
        if glob.has_magic(text):
            matches = sorted(glob.glob(text))
            # Default bash behavior: return literal pattern if no matches
            return matches if matches else text

        return text

    def evaluate_extglob_pattern(self, node: ts.Node) -> BashValue:
        """Evaluate extglob pattern - return raw text for pattern matching."""
        # Don't expand globs here - these are patterns for case statements
        return self._get_text(node)

    def evaluate_variable_name(self, node: ts.Node) -> BashValue:
        """Evaluate variable_name nodes.

        Returns the literal name by default. When _resolve_vars is True (set by
        arithmetic contexts), looks up the variable's value instead.
        """
        var_name = self._get_text(node)
        if self._resolve_vars:
            return self._env.get(var_name, '')
        return var_name

    def evaluate_binary_expression(self, node: ts.Node) -> BashValue:
        """Evaluate binary arithmetic expressions."""
        left_node: ts.Node | None = node.child_by_field_name('left')
        op_node: ts.Node = _expect(node.child_by_field_name('operator'))
        right_nodes: list[ts.Node] = node.children_by_field_name('right')

        op_text = self._get_text(op_node)

        # Handle assignment operators (only if left side is a variable name)
        # In test contexts like [ "$X" = "5" ], left side is a string node, not variable_name
        # In arithmetic contexts (C-style for loops), words that are identifiers are valid targets
        is_word_identifier = (
            self._resolve_vars
            and left_node is not None
            and left_node.type == 'word'
            and self._get_text(left_node).isidentifier()
        )
        is_assignment_target = (
            left_node is not None and left_node.type == 'variable_name'
        ) or is_word_identifier
        if is_assignment_target and op_text in (
            '=',
            '+=',
            '-=',
            '*=',
            '/=',
            '%=',
            '<<=',
            '>>=',
            '&=',
            '|=',
            '^=',
            '**=',
        ):
            var_name = self._get_text(_expect(left_node))

            # Get current value for compound assignments (convert to int)
            if op_text != '=':
                current = _bash_to_int(self.evaluate(_expect(left_node)))
            else:
                current = 0

            # Evaluate right side
            right_val = _bash_to_int(self.evaluate(right_nodes[0]))

            # Compute new value based on operator
            if op_text == '=':
                new_val = right_val
            elif op_text == '+=':
                new_val = current + right_val
            elif op_text == '-=':
                new_val = current - right_val
            elif op_text == '*=':
                new_val = current * right_val
            elif op_text == '/=':
                if right_val != 0:
                    new_val = current // right_val
                else:
                    raise ValueError('Division by zero')
            elif op_text == '%=':
                if right_val != 0:
                    new_val = current % right_val
                else:
                    raise ValueError('Division by zero')
            elif op_text == '<<=':
                new_val = current << right_val
            elif op_text == '>>=':
                new_val = current >> right_val
            elif op_text == '&=':
                new_val = current & right_val
            elif op_text == '|=':
                new_val = current | right_val
            elif op_text == '^=':
                new_val = current ^ right_val
            elif op_text == '**=':
                new_val = current**right_val
            else:
                new_val = 0

            # Store and return
            self._env[var_name] = str(new_val)
            return new_val

        # Non-assignment operators - evaluate both sides
        # Keep as BashValue first; convert to int only for arithmetic operators
        left_val = self.evaluate(left_node) if left_node else 0
        right_val_raw = self.evaluate(right_nodes[0])

        # Test operators (return 1 for true, 0 for false)
        # String comparisons - use raw values, not int-converted
        if op_text == '=':
            # In test context, = is string equality
            return 1 if _bash_to_str(left_val) == _bash_to_str(right_val_raw) else 0
        elif op_text == '==':
            # String/pattern equality (same as = in [[ ]] context)
            return 1 if _bash_to_str(left_val) == _bash_to_str(right_val_raw) else 0
        elif op_text == '!=':
            # String inequality
            return 1 if _bash_to_str(left_val) != _bash_to_str(right_val_raw) else 0
        elif op_text == '=~':
            # Regex match - use raw string values
            try:
                result = (
                    re.search(_bash_to_str(right_val_raw), _bash_to_str(left_val))
                    is not None
                )
                return 1 if result else 0
            except re.error:
                return 0

        # Convert to int for numeric operations
        right_val = _bash_to_int(right_val_raw)

        # Integer comparisons
        if op_text == '-eq':
            return 1 if _bash_to_int(left_val) == right_val else 0
        elif op_text == '-ne':
            return 1 if _bash_to_int(left_val) != right_val else 0
        elif op_text == '-lt':
            return 1 if _bash_to_int(left_val) < right_val else 0
        elif op_text == '-le':
            return 1 if _bash_to_int(left_val) <= right_val else 0
        elif op_text == '-gt':
            return 1 if _bash_to_int(left_val) > right_val else 0
        elif op_text == '-ge':
            return 1 if _bash_to_int(left_val) >= right_val else 0
        # Logical operators (also used in test context)
        elif op_text == '-a':
            # Logical AND in test context - both sides are already evaluated as test results
            return 1 if _bash_to_int(left_val) and right_val else 0
        elif op_text == '-o':
            # Logical OR in test context
            return 1 if _bash_to_int(left_val) or right_val else 0
        # File comparison operators
        elif op_text in ('-nt', '-ot', '-ef'):
            # Get both file paths
            left_path = Path(_bash_to_str(left_val))
            right_path = Path(_bash_to_str(right_val_raw))

            try:
                if op_text == '-nt':
                    # Newer than (compare modification times)
                    if not left_path.exists() or not right_path.exists():
                        return 0
                    return (
                        1
                        if left_path.stat().st_mtime > right_path.stat().st_mtime
                        else 0
                    )
                elif op_text == '-ot':
                    # Older than (compare modification times)
                    if not left_path.exists() or not right_path.exists():
                        return 0
                    return (
                        1
                        if left_path.stat().st_mtime < right_path.stat().st_mtime
                        else 0
                    )
                elif op_text == '-ef':
                    # Same file (compare device and inode)
                    if not left_path.exists() or not right_path.exists():
                        return 0
                    left_stat = left_path.stat()
                    right_stat = right_path.stat()
                    return (
                        1
                        if (
                            left_stat.st_dev == right_stat.st_dev
                            and left_stat.st_ino == right_stat.st_ino
                        )
                        else 0
                    )
                else:
                    return 0
            except OSError:
                return 0

        # Arithmetic operators
        match op_text:
            case '+':
                # Array concatenation: list + str
                if isinstance(left_val, list) and isinstance(right_val, str):
                    return left_val + [right_val]
                # Arithmetic addition: convert both to int
                return _bash_to_int(left_val) + right_val
            case '-':
                return _bash_to_int(left_val) - right_val
            case '*':
                return _bash_to_int(left_val) * right_val
            case '/':
                left_int = _bash_to_int(left_val)
                if right_val != 0:
                    return left_int // right_val
                raise ValueError('Division by zero')
            case '%':
                left_int = _bash_to_int(left_val)
                if right_val != 0:
                    return left_int % right_val
                raise ValueError('Division by zero')
            case '**':
                return _bash_to_int(left_val) ** right_val
            case '<<':
                return _bash_to_int(left_val) << right_val
            case '>>':
                return _bash_to_int(left_val) >> right_val
            case '&':
                return _bash_to_int(left_val) & right_val
            case '|':
                return _bash_to_int(left_val) | right_val
            case '^':
                return _bash_to_int(left_val) ^ right_val
            case '<':
                return 1 if _bash_to_int(left_val) < right_val else 0
            case '>':
                return 1 if _bash_to_int(left_val) > right_val else 0
            case '<=':
                return 1 if _bash_to_int(left_val) <= right_val else 0
            case '>=':
                return 1 if _bash_to_int(left_val) >= right_val else 0
            case '==':
                # In arithmetic context, == is numeric equality
                # String equality is handled earlier with = and == in test context
                return 1 if _bash_to_int(left_val) == right_val else 0
            case '!=':
                return 1 if _bash_to_int(left_val) != right_val else 0
            case '&&':
                return 1 if _bash_to_int(left_val) and right_val else 0
            case '||':
                return 1 if _bash_to_int(left_val) or right_val else 0
            case _:
                return 0

    def evaluate_postfix_expression(self, node: ts.Node) -> BashValue:
        operator_node = _expect(node.child_by_field_name('operator'))
        operator = self._get_text(operator_node)

        operand_node = _expect(
            next((c for c in node.children if c != operator_node and c.is_named), None)
        )
        var_name = self._get_text(operand_node)

        # Read current value directly from environment
        var_value = self._env.get(var_name, 0)
        try:
            current = int(var_value)
        except Exception:
            current = 0

        # Post-increment/decrement returns old value but modifies variable
        if operator == '++':
            self._env[var_name] = str(current + 1)
            return current
        elif operator == '--':
            self._env[var_name] = str(current - 1)
            return current

        return current

    def evaluate_ternary_expression(self, node: ts.Node) -> BashValue:
        cond_node: ts.Node = _expect(node.child_by_field_name('condition'))
        cons_node: ts.Node = _expect(node.child_by_field_name('consequence'))
        alt_node: ts.Node = _expect(node.child_by_field_name('alternative'))

        test_outcome = _bash_to_int(self.evaluate(cond_node))

        if test_outcome == 1:
            return self.evaluate(cons_node)
        else:
            return self.evaluate(alt_node)

    def evaluate_unary_expression(self, node: ts.Node) -> BashValue:
        operator_node = _expect(node.child_by_field_name('operator'))

        op_text = self._get_text(operator_node)
        operand = _expect(
            next((c for c in node.children if c != operator_node and c.is_named), None)
        )

        if operand is None:
            return 0

        # Pre-increment/decrement modify the variable
        if op_text in ('++', '--'):
            var_name = self._get_text(operand)

            # Read current value directly from environment
            var_value = self._env.get(var_name, '0')
            try:
                current = int(var_value)
            except ValueError:
                current = 0

            if op_text == '++':
                new_val = current + 1
            else:  # --
                new_val = current - 1

            self._env[var_name] = str(new_val)
            return new_val

        # Test operators (string tests return 1 for true, 0 for false)
        if op_text == '-z':
            # Zero length string
            operand_str = _bash_to_str(self.evaluate(operand))
            return 1 if len(operand_str) == 0 else 0
        elif op_text == '-n':
            # Non-zero length string
            operand_str = _bash_to_str(self.evaluate(operand))
            return 1 if len(operand_str) > 0 else 0

        # File test operators
        elif op_text in (
            '-e',
            '-f',
            '-d',
            '-r',
            '-w',
            '-x',
            '-s',
            '-L',
            '-h',
            '-b',
            '-c',
            '-p',
            '-S',
            '-G',
            '-O',
            '-N',
            '-k',
            '-t',
            '-u',
            '-g',
        ):
            path_str = _bash_to_str(self.evaluate(operand))
            path = Path(path_str)

            try:
                if op_text == '-e':
                    # File exists (follows symlinks)
                    return 1 if path.exists() else 0
                elif op_text == '-f':
                    # Regular file (follows symlinks)
                    return 1 if path.is_file() else 0
                elif op_text == '-d':
                    # Directory (follows symlinks)
                    return 1 if path.is_dir() else 0
                elif op_text in ('-L', '-h'):
                    # Symbolic link (does not follow symlinks)
                    return 1 if path.is_symlink() else 0
                elif op_text == '-r':
                    # Readable
                    return 1 if os.access(path, os.R_OK) else 0
                elif op_text == '-w':
                    # Writable
                    return 1 if os.access(path, os.W_OK) else 0
                elif op_text == '-x':
                    # Executable
                    return 1 if os.access(path, os.X_OK) else 0
                elif op_text == '-s':
                    # Non-empty file (follows symlinks)
                    return 1 if path.exists() and path.stat().st_size > 0 else 0
                elif op_text == '-b':
                    # Block device
                    return (
                        1 if path.exists() and stat.S_ISBLK(path.stat().st_mode) else 0
                    )
                elif op_text == '-c':
                    # Character device
                    return (
                        1 if path.exists() and stat.S_ISCHR(path.stat().st_mode) else 0
                    )
                elif op_text == '-p':
                    # Named pipe (FIFO)
                    return 1 if path.is_fifo() else 0
                elif op_text == '-S':
                    # Socket
                    return 1 if path.is_socket() else 0
                elif op_text == '-u':
                    # Setuid bit set
                    return (
                        1
                        if path.exists() and (path.stat().st_mode & stat.S_ISUID)
                        else 0
                    )
                elif op_text == '-g':
                    # Setgid bit set
                    return (
                        1
                        if path.exists() and (path.stat().st_mode & stat.S_ISGID)
                        else 0
                    )
                elif op_text == '-k':
                    # Sticky bit set
                    return (
                        1
                        if path.exists() and (path.stat().st_mode & stat.S_ISVTX)
                        else 0
                    )
                elif op_text == '-G':
                    # Owned by effective group ID
                    return (
                        1 if path.exists() and path.stat().st_gid == os.getegid() else 0
                    )
                elif op_text == '-O':
                    # Owned by effective user ID
                    return (
                        1 if path.exists() and path.stat().st_uid == os.geteuid() else 0
                    )
                elif op_text == '-N':
                    # Modified since last read
                    st = path.stat()
                    return 1 if st.st_mtime > st.st_atime else 0
                elif op_text == '-t':
                    # File descriptor is a terminal
                    try:
                        fd = int(path_str)
                        return 1 if os.isatty(fd) else 0
                    except (ValueError, OSError):
                        return 0
                else:
                    return 0
            except (OSError, ValueError):
                # File doesn't exist or can't be accessed
                return 0

        # Other unary operators
        operand_val = _bash_to_int(self.evaluate(operand))

        match op_text:
            case '+':
                return operand_val
            case '-':
                return -operand_val
            case '!':
                return 1 if not operand_val else 0
            case '~':
                return ~operand_val
            case _:
                return 0

    def _apply_redirects(
        self, runnable: ShellRunnable, node: ts.Node
    ) -> ShellRunnable:
        """Apply all redirect nodes to a runnable and return the modified runnable.

        Temp files (e.g., for heredocs) are tracked in self._temp_files for cleanup.
        """
        for redirect_node in node.children_by_field_name('redirect'):
            if redirect_node.type == 'file_redirect':
                dest_nodes = _expect(
                    redirect_node.children_by_field_name('destination')
                )
                dest_file = _bash_to_file(self.evaluate(dest_nodes[0]))

                # Check for >> vs >
                is_append = any(
                    self._get_text(child) == '>>' for child in redirect_node.children
                )

                if is_append:
                    runnable >>= dest_file
                else:
                    runnable = runnable > dest_file
            elif redirect_node.type == 'heredoc_redirect':
                # Check for tab-stripping mode (<<-)
                strip_tabs = any(c.type == '<<-' for c in redirect_node.children)

                # Find heredoc content by extracting between start marker and end marker
                # Tree-sitter's heredoc_body has incorrect byte boundaries (misses leading content)
                start_end = None
                end_start = None
                body_node = None
                for child in redirect_node.children:
                    if child.type == 'heredoc_start':
                        start_end = child.end_byte
                    elif child.type == 'heredoc_end':
                        end_start = child.start_byte
                    elif child.type == 'heredoc_body':
                        body_node = child

                if start_end is not None and end_start is not None:
                    # Extract raw content between markers (skip newline after start)
                    raw_content = self._source[start_end + 1 : end_start]

                    # If there are expansions, we need to evaluate them
                    if body_node is not None and body_node.named_children:
                        # Use evaluate which handles expansions with byte tracking
                        body_content = _bash_to_str(self.evaluate(body_node))
                        # Prepend any content before the body node started
                        prefix = self._source[start_end + 1 : body_node.start_byte]
                        body_content = prefix + body_content
                    else:
                        body_content = raw_content
                else:
                    body_content = ''

                # Strip leading tabs from each line if <<- was used
                if strip_tabs:
                    body_content = '\n'.join(
                        line.lstrip('\t') for line in body_content.split('\n')
                    )

                # Write to temp file and track for cleanup
                with tempfile.NamedTemporaryFile(
                    mode='w', delete=False, suffix='.heredoc'
                ) as f:
                    f.write(body_content)
                    self._temp_files.append(f.name)

                # Set stdin to temp file
                runnable = runnable < self._temp_files[-1]
            elif redirect_node.type == 'herestring_redirect':
                # Here string: <<< "string" - evaluate and pipe to stdin
                string_content = ''
                for child in redirect_node.children:
                    if child.type != '<<<':
                        string_content = _bash_to_str(self.evaluate(child))
                        break

                # Write to temp file (same approach as heredoc)
                with tempfile.NamedTemporaryFile(
                    mode='w', delete=False, suffix='.herestring'
                ) as f:
                    f.write(string_content + '\n')  # Here strings add trailing newline
                    self._temp_files.append(f.name)

                runnable = runnable < self._temp_files[-1]
            else:
                raise NotImplementedError(f'Redirect type {redirect_node.type}')

        return runnable

    def _build_function_runnable(self, func_name: str, args: list[str]) -> ShellRunnable:
        """Build a runnable for a user-defined function call.

        Uses InProcessCallable to enable function execution in pipelines
        and command substitution with proper FD redirection.
        """
        body_node = self._functions[func_name]
        return InProcessCallable(
            lambda: self._execute_function_body(func_name, args, body_node),
            name=func_name,
        )

    def _build_command_runnable(self, cmd_node: ts.Node) -> ShellRunnable:
        name_node = _expect(cmd_node.child_by_field_name('name'))

        cmd_name = self._get_text(name_node)
        cmd_args = []
        for arg_node in cmd_node.children_by_field_name('argument'):
            arg_value = self.evaluate(arg_node)

            # Flatten lists from glob expansion
            if isinstance(arg_value, list):
                cmd_args.extend([_bash_to_str(v) for v in arg_value])
            else:
                cmd_args.append(_bash_to_str(arg_value))

        # Check if it's a user-defined function
        if cmd_name in self._functions:
            return self._build_function_runnable(cmd_name, cmd_args)

        return prog(cmd_name)(*cmd_args)

    def _build_pipeline_runnable(self, pipeline_node: ts.Node) -> ShellRunnable:
        """Build a runnable from a pipeline node by chaining commands."""
        commands = [
            self._node_to_runnable(child)
            for child in pipeline_node.children
            if child.is_named
        ]
        if not commands:
            raise ValueError('Empty pipeline')
        if len(commands) == 1:
            return commands[0]
        # Create Pipeline with pipefail option from shell state
        # Cast is safe: pipeline children are commands, not nested pipelines
        predecessors = cast(list[NotPipeline], commands[:-1])
        final_cmd = cast(NotPipeline, commands[-1])
        return Pipeline(predecessors, final_cmd, pipefail=self._shell_options['pipefail'])

    def _node_to_runnable(self, node: ts.Node) -> ShellRunnable:
        """Convert any executable node to a ShipRunnable.

        This is a general method that handles converting various bash node types
        into ShipShell runnables, centralizing the conversion logic.
        """
        match node.type:
            case 'command':
                return self._build_command_runnable(node)
            case 'pipeline':
                return self._build_pipeline_runnable(node)
            case 'subshell':
                # Extract subshell command text and wrap in bash -c with all vars
                full_text = self._get_text(node)
                commands_text = full_text[1:-1].strip()  # Remove ( and )

                if not commands_text:
                    # Empty subshell - return a no-op
                    return prog('true')()

                # Get ALL current variables (not just exported)
                all_vars = dict(self._env.items())

                # Pass shell options via SHELLOPTS for inheritance
                shellopts = self._build_shellopts()
                if shellopts:
                    all_vars['SHELLOPTS'] = shellopts

                # Return a runnable that executes bash with all variables
                return prog('bash')('-c', commands_text).env(**all_vars)
            case 'negated_command':
                # Get the child node and build a negated runnable
                child_nodes = [c for c in node.children if c.is_named]
                if not child_nodes:
                    raise ValueError('Negated command has no child')
                inner_runnable = self._node_to_runnable(child_nodes[0])
                return inner_runnable.neg()
            case 'redirected_statement':
                # Get the body and build the runnable with redirects applied
                body_node = node.child_by_field_name('body')
                if body_node is None:
                    raise ValueError('Redirected statement has no body')
                runnable = self._node_to_runnable(body_node)
                return self._apply_redirects(runnable, node)
            case 'test_command':
                # Build a test runnable by evaluating the expression
                child = next((c for c in node.children if c.is_named), None)
                if child is None:
                    return prog('false')()
                # Evaluate the expression and return true/false runnable
                result = _bash_to_int(self.evaluate(child))
                return prog('true')() if result else prog('false')()
            case _:
                raise ValueError(f"Cannot convert node type '{node.type}' to runnable")

    def visit_case_statement(self, node: ts.Node):
        """Handle case statements with proper glob pattern matching."""
        # Get the value to match against
        value_node = _expect(node.child_by_field_name('value'))
        value = _bash_to_str(self.evaluate(value_node))

        # Iterate through case items
        for case_item in [n for n in node.children if n.type == 'case_item']:
            # Get all pattern nodes (we need to skip these when visiting body)
            pattern_nodes = case_item.children_by_field_name('value')

            # Check if ANY pattern matches (OR logic)
            matched = False
            for pattern_node in pattern_nodes:
                pattern = _bash_to_str(self.evaluate(pattern_node))
                # Use fnmatch for glob pattern matching (*, ?, [abc], etc.)
                if fnmatch.fnmatch(value, pattern):
                    matched = True
                    break  # Found a match, no need to check other patterns

            # If matched, visit all children EXCEPT the patterns
            if matched:
                for child in case_item.named_children:
                    # Skip pattern nodes (already evaluated)
                    if child not in pattern_nodes:
                        self.visit(child)

                # Handle termination
                if case_item.child_by_field_name('fallthrough'):
                    # ;& or ;;& - continue to next case_item
                    continue
                else:
                    # ;; - default termination, stop processing
                    return

    def _handle_set_command(self, node: ts.Node):
        """Handle the set builtin for shell options and positional params.

        Supports:
        - Short flags: set -e, set +e, set -ex, set -euo
        - Long form: set -o errexit, set +o errexit
        - Combined: set -o errexit -x, set -euo pipefail
        - Positional params: set -- arg1 arg2 (sets $1, $2, etc.)
        """
        args = [self._get_text(arg) for arg in node.children_by_field_name('argument')]

        i = 0
        while i < len(args):
            arg = args[i]

            if arg == '--':
                # set -- args: everything after -- becomes positional params
                self._positional_params = args[i + 1 :]
                break
            elif arg in ('-o', '+o'):
                # -o option or +o option (with space)
                enable = arg[0] == '-'
                i += 1
                if i < len(args):
                    opt_name = args[i]
                    if opt_name in self._shell_options:
                        self._shell_options[opt_name] = enable
            elif arg.startswith(('-o', '+o')):
                # -oerrexit (no space)
                enable = arg[0] == '-'
                opt_name = arg[2:]
                if opt_name in self._shell_options:
                    self._shell_options[opt_name] = enable
            elif arg.startswith(('-', '+')):
                # -e, +e, -ex, -eux, etc.
                enable = arg[0] == '-'
                flags = arg[1:]
                for flag in flags:
                    if flag in FLAG_TO_OPTION:
                        opt_name = FLAG_TO_OPTION[flag]
                        self._shell_options[opt_name] = enable

            i += 1

        self._env.last_exit = 0

    def visit_command(self, node: ts.Node):
        # Intercept special builtins that modify interpreter state
        name_node = node.child_by_field_name('name')
        if name_node:
            cmd_name = self._get_text(name_node)
            if cmd_name == 'set':
                self._handle_set_command(node)
                return
            if cmd_name == 'return':
                self._handle_return_command(node)
                return
            if cmd_name == ':':
                # Colon is a noop builtin that returns 0
                self._env.last_exit = 0
                return

        runnable = self._build_command_runnable(node)
        # Apply any inline redirects (e.g., here strings: cat <<< "hello")
        runnable = self._apply_redirects(runnable, node)
        runnable()

    def _handle_return_command(self, node: ts.Node):
        """Handle the return builtin - exits from a function with an exit code."""
        if not self._call_stack:
            # Not inside a function - bash prints error and continues
            print('bash: return: can only `return` from a function or sourced script',
                  file=sys.stderr)
            self._env.last_exit = 1
            return

        # Get optional exit code argument
        arg_nodes = list(node.children_by_field_name('argument'))
        if arg_nodes:
            exit_code = _bash_to_int(self.evaluate(arg_nodes[0]))
        else:
            # Use last exit code
            exit_code = self._env.last_exit

        raise BashReturnException(exit_code)

    def visit_declaration_command(self, node: ts.Node):
        """Handle export, declare, typeset, readonly, local"""
        # Get the command keyword (first child)
        keyword_node = node.children[0] if node.children else None
        if keyword_node is None:
            return

        keyword = self._get_text(keyword_node)

        # Handle 'local' - only valid inside functions
        if keyword == 'local':
            if not self._call_stack:
                print('bash: local: can only be used in a function', file=sys.stderr)
                self._env.last_exit = 1
                return

            current_frame = self._call_stack[-1]
            for child in node.children:
                if child.type == 'variable_assignment':
                    name_node = child.child_by_field_name('name')
                    value_node = child.child_by_field_name('value')
                    if name_node:
                        name = _bash_to_str(self.evaluate(name_node))
                        value = _bash_to_str(self.evaluate(value_node)) if value_node else ''
                        current_frame.local_vars[name] = value
                elif child.type in ('word', 'variable_name') and child != keyword_node:
                    # local VAR (declare local without value)
                    var_name = self._get_text(child)
                    if var_name not in current_frame.local_vars:
                        current_frame.local_vars[var_name] = ''

            self._env.last_exit = 0
            return

        # Handle variable assignments in the command
        for child in node.children:
            if child.type == 'variable_assignment':
                name_node = child.child_by_field_name('name')
                value_node = child.child_by_field_name('value')

                if name_node is None:
                    continue

                name = _bash_to_str(self.evaluate(name_node))
                value = _bash_to_str(self.evaluate(value_node)) if value_node else ''

                # Set the variable
                self._env[name] = value

                # Mark as exported if it's an export command
                if keyword == 'export':
                    self._env.export(name)
                # TODO: Handle declare -x, typeset -x when needed
            elif child.type in ('word', 'string', 'raw_string', 'variable_name'):
                # export VAR (without assignment - export existing var)
                if keyword == 'export':
                    var_name = _bash_to_str(self._get_text(child))
                    # Only mark as exported if it exists
                    if var_name in self._env:
                        self._env.export(var_name)

    def _execute_c_style_expr(self, node: ts.Node) -> int:
        """Execute a c-style for loop expression/statement and return its integer value.

        C-style for loops can contain both expressions (like i++, i<10) and
        statements (like variable_assignment). This helper handles both cases.
        """
        with self._arithmetic_context():
            if node.type == 'variable_assignment':
                # Execute the assignment and return the assigned value
                self.visit(node)
                name_node = _expect(node.child_by_field_name('name'))
                var_name = self._get_text(name_node)
                return _bash_to_int(self._env.get(var_name, 0))
            else:
                # Regular expression - evaluate and return
                return _bash_to_int(self.evaluate(node))

    def visit_c_style_for_statement(self, node: ts.Node):
        """Handle c-style for loops: `for ((i=0; i<10; i++)); do ...; done`"""
        initializers = node.children_by_field_name('initializer')
        conditions = node.children_by_field_name('condition')
        updates = node.children_by_field_name('update')
        body = _expect(node.child_by_field_name('body'))

        def eval_conditions() -> bool:
            """Evaluate condition expressions - empty means infinite loop (true)"""
            if not conditions:
                return True

            # Comma-separated conditions are ALL evaluated (comma operator behavior)
            # Only the last result determines loop continuation
            # Note: && and || within expressions DO short-circuit via evaluate_binary_expression
            # Default to 1 (truthy) in case there are no named condition nodes
            result = 1
            for condition in [c for c in conditions if c.is_named]:
                result = self._execute_c_style_expr(condition)

            return result != 0

        def run_updates():
            """Execute update expressions"""
            if not updates:
                return
            for update in [u for u in updates if u.is_named]:
                self._execute_c_style_expr(update)

        # Run the initializers if there are any
        if initializers:
            for initializer in [i for i in initializers if i.is_named]:
                self._execute_c_style_expr(initializer)

        # While the condition evaluates to True
        while eval_conditions():
            self.visit_children(body)  # Run the body
            run_updates()  # Execute updates

    def visit_for_statement(self, node: ts.Node):
        """Handle for loops: for var in values; do ...; done"""
        # Get loop variable name
        var_node = _expect(node.child_by_field_name('variable'))
        var_name = self._get_text(var_node)

        # Get values to iterate over
        value_nodes = node.children_by_field_name('value')

        if not value_nodes:
            # No "in" clause - defaults to $@ (positional parameters)
            values = self._positional_params
        else:
            # Expand all value expressions
            values: list[str] = []
            for v_node in value_nodes:
                result = self.evaluate(v_node)

                if isinstance(result, list):
                    # Array expansion (e.g., "${arr[@]}") - each element is separate
                    values.extend(result)
                elif v_node.type == 'command_substitution':
                    # Command substitution output is split on whitespace (IFS)
                    output = _bash_to_str(result)
                    if output:
                        values.extend(output.split())
                else:
                    # Scalar value - single iteration value
                    values.append(_bash_to_str(result))

        # Execute loop body for each value
        body_node = node.child_by_field_name('body')
        if body_node:
            for value in values:
                self._env[var_name] = value
                self.visit_children(body_node)

    def visit_function_definition(self, node: ts.Node):
        """Register a function definition.

        Handles all three syntaxes:
        - function foo { ... }
        - foo() { ... }
        - function foo() { ... }

        All produce a function_definition node with:
        - name: word node containing function name
        - body: compound_statement node containing the function body
        """
        # Extract function name - first word child is the name
        name_node = next((c for c in node.children if c.type == 'word'), None)
        if name_node is None:
            raise BashScriptError('function definition without name')

        func_name = self._get_text(name_node)

        # Extract function body (compound_statement)
        body_node = next((c for c in node.children if c.type == 'compound_statement'), None)
        if body_node is None:
            raise BashScriptError(f'function {func_name} has no body')

        # Store the function (allows redefinition)
        self._functions[func_name] = body_node

        # Function definition returns 0
        self._env.last_exit = 0

    def _visit_elif_clause(self, node: ts.Node) -> bool:
        it = iter(node.children)

        # Run through everything before the "then" node (condition - suppress errexit)
        child = next(it, None)
        with self._suppress_errexit():
            while child and child.type != 'then':
                if child.is_named:
                    self.visit(child)
                child = next(it, None)

        # Return here if the condition doesn't pass
        if self._env.get('?', 0) != 0:
            return False

        # Otherwise run the body and report back that it ran
        while child := next(it, None):
            if child.is_named:
                self.visit(child)
        return True

    def visit_if_statement(self, node: ts.Node):
        it = iter(node.children)

        # Run through everything before the "then" node (condition - suppress errexit)
        child = next(it, None)
        with self._suppress_errexit():
            while child and child.type != 'then':
                if child.is_named:
                    self.visit(child)
                child = next(it, None)
        child = next(it, None)  # Consume the "then" node

        # Work through the body, running statements if condition was successful
        run_body = self._env.get('?', 0) == 0
        while child and child.type not in ('elif_clause', 'else_clause', 'fi'):
            if run_body:
                self.visit(child)
            child = next(it, None)
        if run_body:
            return

        # Try the elif clauses if the initial condition didn't pass
        while child and child.type == 'elif_clause':
            if self._visit_elif_clause(child):
                return
            child = next(it, None)

        if child and child.type == 'else_clause':
            self.visit_children(child)
        else:
            # No branch executed - bash returns 0 in this case
            self._env.last_exit = 0

    def visit_list(self, node: ts.Node):
        # Check if this list contains && or || (errexit suppressed for those)
        has_and_or = any(c.type in ('&&', '||') for c in node.children)

        for child in node.children:
            if child.is_named:
                if has_and_or:
                    with self._suppress_errexit():
                        self.visit(child)
                else:
                    self.visit(child)
            elif child.type == '&&':
                if self._env.get('?', 0) != 0:
                    return
            elif child.type == '||':
                if self._env.get('?', 0) == 0:
                    return

    def visit_negated_command(self, node: ts.Node):
        child_nodes = [c for c in node.children if c.is_named]
        if not child_nodes:
            raise ValueError('Negated command has no child')

        # Build a runnable from the child and negate it (suppress errexit - negation inverts result)
        with self._suppress_errexit():
            runnable = self._node_to_runnable(child_nodes[0])
            runnable.neg()()

    def visit_pipeline(self, node: ts.Node):
        # Build and execute the pipeline using the helper method
        runnable = self._build_pipeline_runnable(node)
        runnable()

    def visit_redirected_statement(self, node: ts.Node):
        # Get the body (the command being redirected)
        body_node = node.child_by_field_name('body')
        if body_node is None:
            raise ValueError('Redirected statement has no body')

        # Build the base runnable from the body
        runnable = self._node_to_runnable(body_node)

        # Apply redirects using the helper
        runnable = self._apply_redirects(runnable, node)

        # Execute the redirected runnable
        runnable()

    def visit_subshell(self, node: ts.Node):
        """Handle bash subshells: (commands)

        Subshells inherit ALL variables (not just exported ones) and execute
        in an isolated environment where changes don't affect the parent.
        """
        # Extract the source text of the subshell (between the parentheses)
        full_text = self._get_text(node)
        commands_text = full_text[1:-1].strip()  # Remove outer ( and )

        if not commands_text:
            # Empty subshell: ()
            self._env.last_exit = 0
            return

        # Get ALL current variables (not just exported ones)
        all_vars = dict(self._env.items())

        # Pass shell options via SHELLOPTS for inheritance
        shellopts = self._build_shellopts()
        if shellopts:
            all_vars['SHELLOPTS'] = shellopts

        # Execute bash with all variables passed via with_env
        sub_bash = prog('bash')('-c', commands_text).env(**all_vars)
        sub_bash()

    # --- Test expression precedence fix (ShipShell-53a) ---
    #
    # tree-sitter-bash parses -a, -o, and ! with incorrect precedence.
    # We fix this by collecting leaf tokens and rebuilding the tree with
    # correct precedence, then evaluating the restructured tree.
    #
    # Precedence (lowest to highest):
    #   1. -o  (OR)
    #   2. -a  (AND)
    #   3. !   (NOT)
    #   4. primaries: comparisons (-eq, =), unary tests (-f, -z)

    _TEST_LOGICAL_OPS = frozenset({'-a', '-o', '!'})
    _TEST_UNARY_OPS = frozenset({
        '-e', '-f', '-d', '-r', '-w', '-x', '-s', '-L', '-h',
        '-b', '-c', '-p', '-S', '-G', '-O', '-N', '-k', '-t', '-u', '-g',
        '-z', '-n',
    })
    _TEST_BINARY_OPS = frozenset({
        '-eq', '-ne', '-lt', '-le', '-gt', '-ge',
        '=', '==', '!=', '<', '>',
        '-nt', '-ot', '-ef', '=~',
    })

    # Expression structure nodes we recurse into; everything else is a leaf
    _TEST_EXPR_NODES = frozenset({'binary_expression', 'unary_expression'})

    def _collect_test_leaves(self, node: ts.Node) -> list[ts.Node]:
        """Collect leaf nodes from test expression in left-to-right order."""
        leaves: list[ts.Node] = []

        def walk(n: ts.Node):
            if n.type in self._TEST_EXPR_NODES:
                for child in n.children:
                    walk(child)
            elif self._get_text(n) not in ('[', ']', '[[', ']]'):
                leaves.append(n)

        walk(node)
        return leaves

    def _test_needs_restructuring(self, node: ts.Node) -> bool:
        """Check if test expression contains -a, -o, or ! needing precedence fix."""
        def has_logical_op(n: ts.Node) -> bool:
            if n.child_count == 0:
                return self._get_text(n) in self._TEST_LOGICAL_OPS
            return any(has_logical_op(c) for c in n.children)
        return has_logical_op(node)

    def _build_test_tree(self, leaves: list[ts.Node]) -> ts.Node | SyntheticNode:
        """Build correctly-structured tree from leaf tokens."""
        node, _ = self._build_test_or(leaves, 0)
        return node

    def _build_test_or(
        self, leaves: list[ts.Node], pos: int
    ) -> tuple[ts.Node | SyntheticNode, int]:
        """Build OR level (-o) - lowest precedence."""
        left, pos = self._build_test_and(leaves, pos)
        while pos < len(leaves) and self._get_text(leaves[pos]) == '-o':
            op = leaves[pos]
            pos += 1
            right, pos = self._build_test_and(leaves, pos)
            left = SyntheticNode('binary_expression', {
                'left': left, 'operator': op, 'right': [right]
            })
        return left, pos

    def _build_test_and(
        self, leaves: list[ts.Node], pos: int
    ) -> tuple[ts.Node | SyntheticNode, int]:
        """Build AND level (-a)."""
        left, pos = self._build_test_not(leaves, pos)
        while pos < len(leaves) and self._get_text(leaves[pos]) == '-a':
            op = leaves[pos]
            pos += 1
            right, pos = self._build_test_not(leaves, pos)
            left = SyntheticNode('binary_expression', {
                'left': left, 'operator': op, 'right': [right]
            })
        return left, pos

    def _build_test_not(
        self, leaves: list[ts.Node], pos: int
    ) -> tuple[ts.Node | SyntheticNode, int]:
        """Build NOT level (!) - right-associative."""
        if pos < len(leaves) and self._get_text(leaves[pos]) == '!':
            op = leaves[pos]
            pos += 1
            inner, pos = self._build_test_not(leaves, pos)
            return SyntheticNode('unary_expression', {'operator': op, 'inner': inner}), pos
        return self._build_test_primary(leaves, pos)

    def _build_test_primary(
        self, leaves: list[ts.Node], pos: int
    ) -> tuple[ts.Node | SyntheticNode, int]:
        """Build primary expressions (unary tests, binary comparisons, values)."""
        if pos >= len(leaves):
            # Shouldn't happen in well-formed input
            return SyntheticNode('word', {}), pos

        token = self._get_text(leaves[pos])

        # Unary test operators: -f file, -z string, etc.
        if token in self._TEST_UNARY_OPS:
            op = leaves[pos]
            pos += 1
            if pos < len(leaves) and self._get_text(leaves[pos]) not in self._TEST_LOGICAL_OPS:
                operand = leaves[pos]
                pos += 1
                return SyntheticNode('unary_expression', {'operator': op, 'operand': operand}), pos
            # Missing operand - return what we have
            return SyntheticNode('unary_expression', {'operator': op}), pos

        # Value - check if followed by binary operator
        left = leaves[pos]
        pos += 1

        if pos < len(leaves) and self._get_text(leaves[pos]) in self._TEST_BINARY_OPS:
            op = leaves[pos]
            pos += 1
            if pos < len(leaves) and self._get_text(leaves[pos]) not in self._TEST_LOGICAL_OPS:
                right = leaves[pos]
                pos += 1
                return SyntheticNode('binary_expression', {
                    'left': left, 'operator': op, 'right': [right]
                }), pos
            # Missing right operand
            return SyntheticNode('binary_expression', {
                'left': left, 'operator': op, 'right': []
            }), pos

        # Single value (true if non-empty, like -n)
        return left, pos

    def visit_test_command(self, node: ts.Node):
        """Execute a test command: [[ expression ]] or [ expression ]

        Restructures the AST to fix tree-sitter-bash's incorrect precedence
        for -a, -o, and ! operators before evaluation.
        """
        child = next((c for c in node.children if c.is_named), None)
        if child is None:
            self._env.last_exit = 1
            return

        # Restructure tree if it contains logical operators with precedence issues
        if self._test_needs_restructuring(child):
            leaves = self._collect_test_leaves(child)
            fixed_tree = self._build_test_tree(leaves)
            result = _bash_to_int(self.evaluate(fixed_tree))
        else:
            result = _bash_to_int(self.evaluate(child))

        self._env.last_exit = 0 if result else 1

    def visit_compound_statement(self, node: ts.Node):
        """Execute arithmetic compound statement: (( expression ))"""
        with self._arithmetic_context():
            # Find the expression inside (( ... ))
            for child in node.children:
                if child.is_named:
                    # Evaluate the expression (handles assignments, arithmetic, etc.)
                    result = _bash_to_int(self.evaluate(child))
                    # Exit code: 0 if non-zero result, 1 if zero
                    self._env.last_exit = 0 if result else 1
                    return
            self._env.last_exit = 0

    def visit_unset_command(self, node: ts.Node):
        """Handle unset command to remove variables"""
        # unset command removes variables from the environment
        # Usage: unset VAR1 VAR2 VAR3
        for child in node.children:
            if child.type in ('word', 'variable_name'):
                var_name = self._get_text(child)
                # Use del to remove from environment (calls __delitem__)
                if var_name in self._env:
                    del self._env[var_name]

    def visit_variable_assignment(self, node: ts.Node):
        """Handle variable assignment: var=value"""
        name_node = _expect(node.child_by_field_name('name'))
        value_node = node.child_by_field_name('value')

        name = self._get_text(name_node)
        if value_node:
            value = self.evaluate(value_node)
            # Preserve arrays as-is, convert other types to strings
            if not isinstance(value, list):
                value = _bash_to_str(value)
        else:
            value = ''

        # Set variable (respects local scope if in a function)
        self._set_variable(name, value)

    def visit_while_statement(self, node: ts.Node):
        # Loop while condition is true (exit code 0)
        while True:
            # Execute condition (suppress errexit - condition failure is expected)
            condition_node = node.child_by_field_name('condition')
            if condition_node:
                with self._suppress_errexit():
                    self.visit(condition_node)

            # If condition failed (non-zero exit), exit loop
            if self._env.last_exit != 0:
                break

            # Execute body (do_group)
            body_node = node.child_by_field_name('body')
            if body_node:
                self.visit_children(body_node)

        # While loop that never executes body returns 0
        self._env.last_exit = 0


def print_bash_tree(bash_code: str, show_unnamed: bool = False):
    """Parse bash code and print the tree structure with node types.

    Args:
        bash_code: A string containing bash code to parse
        show_unnamed: If True, show unnamed nodes (punctuation, keywords);
            if False, only named nodes
    """
    # Create the bash language and parser
    bash_language = Language(tsbash.language())
    parser = Parser(bash_language)

    # Parse the bash code
    tree = parser.parse(bytes(bash_code, 'utf-8'))

    def print_node(node: ts.Node, indent: int = 0):
        """Recursively print a node and its children with indentation."""
        # Skip unnamed nodes if requested
        if not show_unnamed and not node.is_named:
            for child in node.children:
                print_node(child, indent)
            return

        # Print current node
        indent_str = '  ' * indent
        node_info = f'{indent_str}{node.type}'

        # Add text content for leaf nodes or small nodes
        if node.child_count == 0 or len(node.children) == 0:
            text = bash_code[node.start_byte : node.end_byte]
            if len(text) <= 40:  # Only show short text
                node_info += f' [{repr(text)}]'

        print(node_info)

        # Recursively print children
        for child in node.children:
            print_node(child, indent + 1)

    # Print the tree starting from root
    print_node(tree.root_node)


def _wire_functions(
    interpreter: ShipBashInterpreter,
    scope: str | None,
) -> None:
    """Wire bash functions from interpreter into Python namespace.

    Creates callable wrappers that hold a reference to the interpreter,
    allowing efficient function calls without creating new interpreters.
    The interpreter stays alive as long as any wired function exists.

    Functions with invalid Python identifiers or Python keywords are skipped
    with a warning. Functions that shadow Python builtins are allowed but
    warned about.
    """
    import builtins
    import keyword
    import types
    import warnings

    import __main__

    # Get or create target namespace
    if scope is None or scope == '__main__':
        target_ns = __main__.__dict__
    else:
        if hasattr(__main__, scope):
            module = getattr(__main__, scope)
        else:
            module = types.ModuleType(scope)
            setattr(__main__, scope, module)
        target_ns = module.__dict__

    # Create callable wrapper for each function
    for func_name, body_node in interpreter._functions.items():
        # Transform name to valid Python identifier
        py_name = func_name.replace('-', '_')  # my-func -> my_func

        # Handle names starting with digits by prefixing with underscore
        if py_name and py_name[0].isdigit():
            py_name = '_' + py_name  # 123func -> _123func

        # Append underscore for Python keywords (like exit_ for exit builtin)
        if keyword.iskeyword(py_name):
            py_name = py_name + '_'  # None -> None_

        # Final validation - skip if still invalid
        if not py_name.isidentifier():
            warnings.warn(
                f"Bash function '{func_name}' cannot be converted to valid Python "
                f"identifier; skipping wiring",
                stacklevel=3,
            )
            continue

        # Warn about transformations
        if py_name != func_name:
            warnings.warn(
                f"Bash function '{func_name}' wired as '{py_name}'",
                stacklevel=3,
            )

        # Warn about builtin shadowing
        if hasattr(builtins, py_name):
            warnings.warn(
                f"Bash function '{func_name}' shadows Python builtin '{py_name}'",
                stacklevel=3,
            )
            # Still wire it - user may intentionally want to override

        # Create a closure that captures the interpreter and body node
        def make_wrapper(interp: ShipBashInterpreter, name: str, body: ts.Node):
            def wrapper(*args: str) -> int:
                """Call bash function with arguments. Returns exit code."""
                return interp._execute_function_body(name, [str(a) for a in args], body)

            wrapper.__name__ = name
            wrapper.__doc__ = f'Bash function: {name}'
            return wrapper

        target_ns[py_name] = make_wrapper(interpreter, func_name, body_node)


def run_bash_code(
    bash_code: str,
    args: list[str] | None = None,
    script_name: str = 'bash',
    env: ShellEnvironment = global_env,
    scope: str | None = '__main__',
):
    """Parse and execute bash code using the ShipBashInterpreter.

    Args:
        bash_code: A string containing bash code to parse and execute
        args: Optional list of positional parameters ($1, $2, etc.)
        script_name: Name of the script ($0), defaults to "bash"
        env: Shell environment to use (defaults to global environment)
        scope: Python namespace to wire functions into. Defaults to __main__.
               Use None to skip wiring. Use a module name like 'f' to wire
               functions as f.foo(), f.bar(), etc.
    """
    # Create the bash language and parser
    bash_language = Language(tsbash.language())
    parser = Parser(bash_language)

    # Parse the bash code
    tree = parser.parse(bytes(bash_code, 'utf-8'))

    # Create interpreter and execute
    interpreter = ShipBashInterpreter(bash_code, args, script_name, env)
    try:
        interpreter.visit(tree.root_node)
    except BashScriptError as e:
        print(f'bash: {e}', file=sys.stderr)
        env.last_exit = e.exit_code

    # Wire defined functions into Python namespace
    if scope is not None and interpreter._functions:
        # Wired functions hold reference to interpreter; cleanup via __del__
        _wire_functions(interpreter, scope)
    else:
        # No wiring, cleanup immediately
        interpreter.cleanup()
