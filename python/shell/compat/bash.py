from __future__ import annotations

from typing import Callable, TYPE_CHECKING

import tree_sitter_bash as tsbash
from tree_sitter import Language, Parser

import shp

if TYPE_CHECKING:
    import tree_sitter as ts


class BashCSTVisitor:
    def visit(self, node: ts.Node):
        method_name = f"visit_{node.type}"
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


class ShipBashInterpreter(BashCSTVisitor):
    def __init__(
        self, source: str, args: list[str] | None = None, script_name: str = "bash"
    ):
        self._source = source
        self._positional_params = args if args is not None else []
        self._script_name = script_name

    def _get_text(self, node: ts.Node) -> str:
        return self._source[node.start_byte : node.end_byte]

    def _expand_string(self, node: ts.Node) -> str:
        parts: list[str] = []
        for child in [c for c in node.children if c.is_named]:
            match child.type:
                case "arithmetic_expansion":
                    raise NotImplementedError("arithmetic_expansion")
                case "command_substitution":
                    parts.append(self._expand_command_substitution(child))
                case "expansion":
                    parts.append(self._expand_expansion(child))
                case "simple_expansion":
                    parts.append(self._expand_simple_expansion(child))
                case "string_content":
                    parts.append(self._get_text(child))
                case fallback:
                    raise ValueError(f"Can't expand string of type '{fallback}'")
        return "".join(parts)

    def _expand_simple_expansion(self, node: ts.Node) -> str:
        """Handle $var expansions including positional parameters"""
        # simple_expansion contains a variable_name or special_variable_name child
        for child in node.children:
            if child.is_named:
                var_name = self._get_text(child)

                # Handle positional parameters
                if var_name == "0":
                    return self._script_name
                elif var_name in ("@", "*"):
                    # Default: space-joined string (context-specific handling elsewhere)
                    return " ".join(self._positional_params)
                elif var_name == "#":
                    return str(len(self._positional_params))
                elif var_name.isdigit():
                    # $1, $2, ..., $9
                    idx = int(var_name) - 1
                    if 0 <= idx < len(self._positional_params):
                        return self._positional_params[idx]
                    return ""

                # Regular environment variables
                return str(shp.env.get(var_name, ""))
        return ""

    def _expand_expansion(self, node: ts.Node) -> str:
        """Handle ${var} and other complex expansions including ${10}, ${11}, etc."""
        # For now, just handle simple ${var}
        # TODO: Handle parameter expansions like ${var:-default}, ${#var}, etc.
        for child in node.children:
            if child.type == "variable_name":
                var_name = self._get_text(child)

                # Check if it's a numeric positional parameter (${10}, ${11}, etc.)
                if var_name.isdigit():
                    idx = int(var_name) - 1
                    if 0 <= idx < len(self._positional_params):
                        return self._positional_params[idx]
                    return ""

                # Regular environment variable
                return str(shp.env.get(var_name, ""))
            elif child.type == "special_variable_name":
                var_name = self._get_text(child)

                # Handle positional parameters
                if var_name == "0":
                    return self._script_name
                elif var_name in ("@", "*"):
                    return " ".join(self._positional_params)
                elif var_name == "#":
                    return str(len(self._positional_params))

                # Other special variables (from environment)
                return str(shp.env.get(var_name, ""))
        return ""

    def _expand_command_substitution(self, node: ts.Node) -> str:
        """Handle command substitution: $(command) or `command`.

        Args:
            node: The command_substitution node

        Returns:
            The captured stdout from the command (with trailing newlines stripped)
        """
        # Find the command node (skip the $( and ) tokens)
        command_node = next(
            (
                c
                for c in node.children
                if c.is_named and c.type != "command_substitution"
            ),
            None,
        )

        if command_node is None:
            # Empty command substitution
            return ""

        # Build a runnable from the command
        runnable = self._node_to_runnable(command_node)

        # Capture the output using shp.get_stdout
        output = shp.get_stdout(runnable)

        # Bash strips trailing newlines from command substitutions
        return output.rstrip("\n")

    def _expand_heredoc_body(self, node: ts.Node, expand: bool = True) -> str:
        """Expand heredoc body with variable/command substitution.

        Args:
            node: The heredoc_body node to expand
            expand: If False, disable all expansions (for quoted delimiters)

        Returns:
            The expanded heredoc content as a string
        """
        # If no expansion needed (quoted delimiter), return raw text
        if not expand:
            return self._get_text(node)

        # Otherwise, process children for variable/command expansion
        # If heredoc_body has no children, it's plain text
        if not node.children or len(node.children) == 0:
            return self._get_text(node)

        parts: list[str] = []
        current_pos = node.start_byte

        # Process ALL children and extract any gaps between them
        for child in node.children:
            # If there's a gap before this child, extract it
            if child.start_byte > current_pos:
                gap_text = self._source[current_pos : child.start_byte]
                parts.append(gap_text)

            # Process the child node
            if child.is_named:
                match child.type:
                    case "heredoc_content":
                        # Plain text content
                        parts.append(self._get_text(child))
                    case "simple_expansion":
                        # $var expansion
                        parts.append(self._expand_simple_expansion(child))
                    case "expansion":
                        # ${var} expansion
                        parts.append(self._expand_expansion(child))
                    case "command_substitution":
                        # $(command) or `command` expansion
                        parts.append(self._expand_command_substitution(child))
                    case "arithmetic_expansion":
                        raise NotImplementedError("arithmetic_expansion in heredoc")
                    case _:
                        # Unknown named node type - extract text
                        parts.append(self._get_text(child))
            else:
                # Unnamed nodes (newlines, whitespace) - preserve as-is
                parts.append(self._get_text(child))

            # Move current position past this child
            current_pos = child.end_byte

        # Extract any trailing text after the last child
        if current_pos < node.end_byte:
            trailing_text = self._source[current_pos : node.end_byte]
            parts.append(trailing_text)

        return "".join(parts)

    def _strip_leading_tabs(self, content: str) -> str:
        """Strip leading tabs from each line (for <<- operator).

        Note: Only strips TABS, not spaces (bash behavior).

        Args:
            content: The heredoc content

        Returns:
            Content with leading tabs removed from each line
        """
        lines = content.split("\n")
        return "\n".join(line.lstrip("\t") for line in lines)

    def _expand_primary_expression(self, node: ts.Node) -> str:
        match node.type:
            # TODO: Other primary expression types
            case "concatenation":
                # TODO: Array and variable_name sub-types
                return "".join(
                    [
                        self._expand_primary_expression(node)
                        for node in node.children
                        if node.is_named
                    ]
                )
            case "raw_string":
                # Single-quoted string
                return self._get_text(node)[1:-1]
            case "string":
                # Double-quoted string (expands)
                return self._expand_string(node)
            case "word" | "number" | "regex":
                # Plain text, numbers, or regex patterns - return as-is
                return self._get_text(node)
            case "simple_expansion":
                # $var expansion
                return self._expand_simple_expansion(node)
            case "expansion":
                # ${var} expansion
                return self._expand_expansion(node)
            case "command_substitution":
                # $(command) or `command` expansion
                return self._expand_command_substitution(node)
            case fallback:
                raise ValueError(f"Can't expand node of type '{fallback}'")
        return ""

    def _get_command_args(self, cmd_node: ts.Node) -> list[str]:
        result: list[str] = []
        for child in cmd_node.children_by_field_name("argument"):
            if child.type in (
                "word",
                "string",
                "raw_string",
                "concatenation",
                "number",
            ):
                result.append(self._expand_primary_expression(child))
            elif child.type == "simple_expansion":
                # Check if it's $@ - expand to separate args
                var_name = self._get_expansion_var_name(child)
                if var_name in ("@", "*"):
                    result.extend(self._positional_params)
                else:
                    result.append(self._expand_simple_expansion(child))
            elif child.type == "expansion":
                # Check if it's ${@} - expand to separate args
                var_name = self._get_expansion_var_name(child)
                if var_name in ("@", "*"):
                    result.extend(self._positional_params)
                else:
                    result.append(self._expand_expansion(child))
            elif child.type == "command_substitution":
                # $(command) or `command` expansion
                result.append(self._expand_command_substitution(child))
            else:
                raise ValueError(
                    f"Can't expand node of type {child.type} for command args"
                )
        return result

    def _get_expansion_var_name(self, node: ts.Node) -> str:
        """Extract variable name from simple_expansion or expansion node"""
        for child in node.children:
            if child.type in ("variable_name", "special_variable_name"):
                return self._get_text(child)
        return ""

    def _apply_redirects(
        self, runnable: shp.ShipRunnable, node: ts.Node
    ) -> shp.ShipRunnable:
        """Apply all redirect nodes to a runnable and return the modified runnable."""
        for redirect_node in node.children_by_field_name("redirect"):
            if redirect_node.type == "file_redirect":
                dest_nodes = redirect_node.children_by_field_name("destination")
                if not dest_nodes:
                    raise ValueError("File redirect has no destination")

                dest_file = self._expand_primary_expression(dest_nodes[0])

                # Check for >> vs >
                is_append = any(
                    self._get_text(child) == ">>" for child in redirect_node.children
                )

                if is_append:
                    runnable >>= dest_file
                else:
                    runnable = runnable > dest_file
            elif redirect_node.type == "heredoc_redirect":
                # Get the operator (<< or <<-)
                operator_text = None
                for child in redirect_node.children:
                    text = self._get_text(child)
                    if text in ("<<", "<<-"):
                        operator_text = text
                        break

                # Get heredoc_start to check for quoted delimiters
                start_node = next(
                    (c for c in redirect_node.children if c.type == "heredoc_start"),
                    None,
                )
                delimiter = self._get_text(start_node) if start_node else ""

                # Check if delimiter is quoted (disables variable expansion)
                # Quoted forms: 'EOF', "EOF", \EOF
                expand = not any(q in delimiter for q in ["'", '"', "\\"])

                # Get and expand the heredoc body
                body_node = next(
                    (c for c in redirect_node.children if c.type == "heredoc_body"),
                    None,
                )
                if body_node:
                    content = self._expand_heredoc_body(body_node, expand=expand)

                    # Strip leading tabs if <<- operator
                    if operator_text == "<<-":
                        content = self._strip_leading_tabs(content)

                    # Pipe heredoc content to command via printf
                    # Use printf to preserve exact formatting (echo interprets escapes)
                    # This provides the heredoc as stdin to the command
                    runnable = shp.prog("printf")("%s", content) | runnable
            else:
                raise NotImplementedError(f"Redirect type {redirect_node.type}")

        return runnable

    def _build_command_runnable(self, cmd_node: ts.Node) -> shp.ShipRunnable:
        name_node = cmd_node.child_by_field_name("name")
        if name_node is None:
            raise ValueError("Command node has no name")
        cmd_name = self._get_text(name_node)
        cmd_args = self._get_command_args(cmd_node)
        return shp.prog(cmd_name)(*cmd_args)

    def _build_pipeline_runnable(self, pipeline_node: ts.Node) -> shp.ShipRunnable:
        """Build a runnable from a pipeline node by chaining commands."""
        runnable = None
        for cmd in [child for child in pipeline_node.children if child.is_named]:
            if runnable is None:
                runnable = self._node_to_runnable(cmd)
            else:
                runnable |= self._node_to_runnable(cmd)
        if runnable is None:
            raise ValueError("Empty pipeline")
        return runnable

    def _node_to_runnable(self, node: ts.Node) -> shp.ShipRunnable:
        """Convert any executable node to a ShipRunnable.

        This is a general method that handles converting various bash node types
        into ShipShell runnables, centralizing the conversion logic.
        """
        match node.type:
            case "command":
                return self._build_command_runnable(node)
            case "pipeline":
                return self._build_pipeline_runnable(node)
            case "subshell":
                # Extract subshell command text and wrap in bash -c with all vars
                full_text = self._get_text(node)
                commands_text = full_text[1:-1].strip()  # Remove ( and )

                if not commands_text:
                    # Empty subshell - return a no-op
                    return shp.prog("true")()

                # Get ALL current variables (not just exported)
                all_vars = dict(shp.env.items())

                # Return a runnable that executes bash with all variables
                return shp.prog("bash")("-c", commands_text).with_env(**all_vars)
            case "negated_command":
                # Get the child node and build a negated runnable
                child_nodes = [c for c in node.children if c.is_named]
                if not child_nodes:
                    raise ValueError("Negated command has no child")
                inner_runnable = self._node_to_runnable(child_nodes[0])
                return inner_runnable.negated()
            case "redirected_statement":
                # Get the body and build the runnable with redirects applied
                body_node = node.child_by_field_name("body")
                if body_node is None:
                    raise ValueError("Redirected statement has no body")
                runnable = self._node_to_runnable(body_node)
                return self._apply_redirects(runnable, node)
            case "test_command":
                # Build a test runnable from the expression
                child = next((c for c in node.children if c.is_named), None)
                if child is None:
                    return shp.prog("false")()
                return self._build_test_runnable_from_expression(child)
            case _:
                raise ValueError(f"Cannot convert node type '{node.type}' to runnable")

    def visit_case_statement(self, node: ts.Node):
        # Value to match against
        value_node = node.child_by_field_name("value")
        if value_node is None:
            raise ValueError("Missing value in case statement")
        value = self._expand_primary_expression(value_node)

        # Work through the case_items and execute matches
        for case_item in [n for n in node.children if n.type == "case_item"]:
            for item in case_item.children_by_field_name("value"):
                if case_item.child_by_field_name("fallthrough"):
                    raise NotImplementedError("case_item fallthrough")
                executed = False

                # Default branch
                if item.type == "extglob_pattern":
                    self.visit_children(case_item)
                    executed = True

                # "Normal" branch
                item_value = self._expand_primary_expression(item)
                if value == item_value:
                    self.visit_children(case_item)
                    executed = True

                # Termination statements prevent continuing
                if executed and case_item.child_by_field_name("termination"):
                    return

    def visit_command(self, node: ts.Node):
        runnable = self._build_command_runnable(node)
        runnable()

    def visit_declaration_command(self, node: ts.Node):
        """Handle export, declare, typeset, readonly, local"""
        # Get the command keyword (first child)
        keyword_node = node.children[0] if node.children else None
        if keyword_node is None:
            return

        keyword = self._get_text(keyword_node)

        # Handle variable assignments in the command
        for child in node.children:
            if child.type == "variable_assignment":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")

                if name_node is None:
                    continue

                name = self._get_text(name_node)
                value = (
                    self._expand_primary_expression(value_node) if value_node else ""
                )

                # Set the variable
                shp.env[name] = value

                # Mark as exported if it's an export command
                if keyword == "export":
                    shp.mark_var_exported(name)
                # TODO: Handle declare -x, typeset -x when needed
                # TODO: Handle local when implementing functions
            elif child.type in ("word", "string", "raw_string", "variable_name"):
                # export VAR (without assignment - export existing var)
                if keyword == "export":
                    var_name = self._get_text(child)
                    # Only mark as exported if it exists
                    if var_name in shp.env:
                        shp.mark_var_exported(var_name)

    def _evaluate_arithmetic_expression(self, node: ts.Node) -> int:
        """Recursively evaluate an arithmetic expression to an integer.

        Handles bash arithmetic expressions including binary operators,
        unary operators, assignments, variable references, etc.
        """
        match node.type:
            case "binary_expression":
                return self._eval_arithmetic_binary(node)
            case "unary_expression":
                return self._eval_arithmetic_unary(node)
            case "postfix_expression":
                return self._eval_arithmetic_postfix(node)
            case "parenthesized_expression":
                # Unwrap parentheses and evaluate inner expression
                inner = next((c for c in node.children if c.is_named), None)
                if inner is None:
                    return 0
                return self._evaluate_arithmetic_expression(inner)
            case "number":
                # Extract numeric value
                num_text = self._get_text(node)
                try:
                    return int(num_text)
                except ValueError:
                    return 0
            case "variable_name" | "word":
                # Read variable from environment and convert to int
                # Both variable_name and word can represent variables in arithmetic context
                var_name = self._get_text(node)
                var_value = shp.env.get(var_name, "0")
                try:
                    return int(var_value)
                except ValueError:
                    return 0
            case "variable_assignment":
                # Handle assignment: var=expr
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")

                if name_node is None or value_node is None:
                    return 0

                # Evaluate the value
                value = self._evaluate_arithmetic_expression(value_node)

                # Store in environment as string
                var_name = self._get_text(name_node)
                shp.env[var_name] = str(value)

                # Return the assigned value
                return value
            case _:
                # Unknown node type - try to parse as number
                text = self._get_text(node)
                try:
                    return int(text)
                except ValueError:
                    return 0

    def _eval_arithmetic_binary(self, node: ts.Node) -> int:
        """Evaluate binary arithmetic expressions."""
        left_node = node.child_by_field_name("left")
        operator_node = node.child_by_field_name("operator")
        right_nodes = node.children_by_field_name("right")

        if operator_node is None:
            return 0

        op_text = self._get_text(operator_node)

        # Handle assignment operators
        if op_text in (
            "=",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "<<=",
            ">>=",
            "&=",
            "|=",
            "^=",
            "**=",
        ):
            # Left must be a variable_name, word, or subscript
            if left_node is None or left_node.type not in ("variable_name", "word"):
                return 0

            var_name = self._get_text(left_node)

            # Get current value for compound assignments
            if op_text != "=":
                current = self._evaluate_arithmetic_expression(left_node)
            else:
                current = 0

            # Evaluate right side
            right_val = (
                self._evaluate_arithmetic_expression(right_nodes[0])
                if right_nodes
                else 0
            )

            # Compute new value based on operator
            if op_text == "=":
                new_val = right_val
            elif op_text == "+=":
                new_val = current + right_val
            elif op_text == "-=":
                new_val = current - right_val
            elif op_text == "*=":
                new_val = current * right_val
            elif op_text == "/=":
                new_val = current // right_val if right_val != 0 else 0
            elif op_text == "%=":
                new_val = current % right_val if right_val != 0 else 0
            elif op_text == "<<=":
                new_val = current << right_val
            elif op_text == ">>=":
                new_val = current >> right_val
            elif op_text == "&=":
                new_val = current & right_val
            elif op_text == "|=":
                new_val = current | right_val
            elif op_text == "^=":
                new_val = current ^ right_val
            elif op_text == "**=":
                new_val = current**right_val
            else:
                new_val = 0

            # Store and return
            shp.env[var_name] = str(new_val)
            return new_val

        # Non-assignment operators - evaluate both sides
        left_val = self._evaluate_arithmetic_expression(left_node) if left_node else 0
        right_val = (
            self._evaluate_arithmetic_expression(right_nodes[0]) if right_nodes else 0
        )

        # Arithmetic operators
        match op_text:
            case "+":
                return left_val + right_val
            case "-":
                return left_val - right_val
            case "*":
                return left_val * right_val
            case "/":
                return left_val // right_val if right_val != 0 else 0
            case "%":
                return left_val % right_val if right_val != 0 else 0
            case "**":
                return left_val**right_val
            case "<<":
                return left_val << right_val
            case ">>":
                return left_val >> right_val
            case "&":
                return left_val & right_val
            case "|":
                return left_val | right_val
            case "^":
                return left_val ^ right_val
            case "<":
                return 1 if left_val < right_val else 0
            case ">":
                return 1 if left_val > right_val else 0
            case "<=":
                return 1 if left_val <= right_val else 0
            case ">=":
                return 1 if left_val >= right_val else 0
            case "==":
                return 1 if left_val == right_val else 0
            case "!=":
                return 1 if left_val != right_val else 0
            case "&&":
                return 1 if left_val and right_val else 0
            case "||":
                return 1 if left_val or right_val else 0
            case _:
                return 0

    def _eval_arithmetic_unary(self, node: ts.Node) -> int:
        """Evaluate unary arithmetic expressions."""
        operator_node = node.child_by_field_name("operator")

        if operator_node is None:
            return 0

        op_text = self._get_text(operator_node)
        operand = next(
            (c for c in node.children if c != operator_node and c.is_named), None
        )

        if operand is None:
            return 0

        # Pre-increment/decrement modify the variable
        if op_text in ("++", "--"):
            if operand.type != "variable_name":
                return 0

            var_name = self._get_text(operand)
            # Read current value directly from environment
            var_value = shp.env.get(var_name, "0")
            try:
                current = int(var_value)
            except ValueError:
                current = 0

            if op_text == "++":
                new_val = current + 1
            else:  # --
                new_val = current - 1

            shp.env[var_name] = str(new_val)
            return new_val

        # Other unary operators
        operand_val = self._evaluate_arithmetic_expression(operand)

        match op_text:
            case "+":
                return operand_val
            case "-":
                return -operand_val
            case "!":
                return 1 if not operand_val else 0
            case "~":
                return ~operand_val
            case _:
                return 0

    def _eval_arithmetic_postfix(self, node: ts.Node) -> int:
        """Evaluate postfix expressions (e.g., i++, i--)."""
        operator_node = node.child_by_field_name("operator")

        if operator_node is None:
            return 0

        op_text = self._get_text(operator_node)
        operand = next(
            (c for c in node.children if c != operator_node and c.is_named), None
        )

        if operand is None:
            return 0

        # Operand can be either "variable_name" or "word" type
        if operand.type not in ("variable_name", "word"):
            return 0

        var_name = self._get_text(operand)
        # Read current value directly from environment
        var_value = shp.env.get(var_name, "0")
        try:
            current = int(var_value)
        except ValueError:
            current = 0

        # Post-increment/decrement returns old value but modifies variable
        if op_text == "++":
            shp.env[var_name] = str(current + 1)
            return current
        elif op_text == "--":
            shp.env[var_name] = str(current - 1)
            return current

        return current

    def visit_c_style_for_statement(self, node: ts.Node):
        """Handle c-style for loops: `for ((i=0; i<10; i++)); do ...; done`"""
        initializers = node.children_by_field_name("initializer")
        conditions = node.children_by_field_name("condition")
        updates = node.children_by_field_name("update")
        body = node.child_by_field_name("body")

        if body is None:
            raise ValueError("c_style_for_statement must have a body")

        def eval_conditions() -> bool:
            """Evaluate condition expressions - empty means infinite loop (true)"""
            if not conditions:
                return True

            # Evaluate all conditions (comma-separated)
            # Return false if any evaluate to 0
            result = 0
            for condition in [c for c in conditions if c.is_named]:
                result = self._evaluate_arithmetic_expression(condition)

            # Return true if last result is non-zero
            return result != 0

        def run_updates():
            """Execute update expressions"""
            if not updates:
                return
            for update in [u for u in updates if u.is_named]:
                self._evaluate_arithmetic_expression(update)

        # Run the initializers if there are any
        if initializers:
            for initializer in [i for i in initializers if i.is_named]:
                self._evaluate_arithmetic_expression(initializer)

        # While the condition evaluates to True
        while eval_conditions():
            self.visit_children(body)  # Run the body
            run_updates()  # Execute updates

    def visit_for_statement(self, node: ts.Node):
        """Handle for loops: for var in values; do ...; done"""
        # Get loop variable name
        var_node = node.child_by_field_name("variable")
        if var_node is None:
            raise ValueError("for statement missing variable")
        var_name = self._get_text(var_node)

        # Get values to iterate over
        value_nodes = node.children_by_field_name("value")

        if not value_nodes:
            # No "in" clause - defaults to $@ (positional parameters)
            values = self._positional_params
        else:
            # Expand all value expressions
            values = []
            for v_node in value_nodes:
                # Check if it's $@ or $* - expand to multiple values
                if v_node.type in ("simple_expansion", "expansion"):
                    var_name_check = self._get_expansion_var_name(v_node)
                    if var_name_check in ("@", "*"):
                        values.extend(self._positional_params)
                        continue
                # Check if it's command substitution - split on whitespace (IFS)
                elif v_node.type == "command_substitution":
                    output = self._expand_command_substitution(v_node)
                    # Split on whitespace (default IFS behavior)
                    # Empty output produces no items
                    if output:
                        values.extend(output.split())
                    continue
                # Regular value - expand and append
                values.append(self._expand_primary_expression(v_node))

        # Execute loop body for each value
        body_node = node.child_by_field_name("body")
        if body_node:
            for value in values:
                shp.env[var_name] = value
                self.visit_children(body_node)

    def visit_function_definition(self, node: ts.Node):
        raise NotImplementedError("function_definition")

    def visit_if_statement(self, node: ts.Node):
        # Execute condition statements
        condition_nodes = node.children_by_field_name("condition")
        for cond_node in condition_nodes:
            self.visit(cond_node)

        # Check the exit code from the last condition
        exit_code = shp.env.get("?", 0)

        # If condition succeeded (exit code 0), execute consequence
        if exit_code == 0:
            # Execute consequence (named children that aren't elif/else clauses)
            for child in node.children:
                if child.is_named and child.type not in ("elif_clause", "else_clause"):
                    # Skip condition nodes, only visit consequence
                    if child not in condition_nodes:
                        self.visit(child)
        else:
            # Check for elif clauses
            for child in node.children:
                if child.type == "elif_clause":
                    # Recursively visit elif (it contains conditions and statements)
                    self.visit_children(child)
                    # If this elif succeeded, we're done
                    if shp.env.get("?", 0) == 0:
                        return

            # If no elif succeeded, execute else clause if present
            for child in node.children:
                if child.type == "else_clause":
                    self.visit_children(child)
                    return

    def visit_list(self, node: ts.Node):
        # List contains statements separated by operators (;, &&, ||, &)
        # We need to look at ALL children to detect operators
        children = list(node.children)

        i = 0
        while i < len(children):
            child = children[i]

            # Visit statement nodes
            if child.is_named:
                self.visit(child)

                # Look ahead for operator
                if i + 1 < len(children):
                    # Skip to operator token (might be whitespace/comments before it)
                    operator_idx = i + 1
                    while (
                        operator_idx < len(children) and children[operator_idx].is_named
                    ):
                        operator_idx += 1

                    if operator_idx < len(children):
                        operator = children[operator_idx]
                        operator_text = self._get_text(operator)

                        if operator_text == "&":
                            raise NotImplementedError(
                                "Async execution (background &) not supported"
                            )
                        elif operator_text == "&&":
                            # Short-circuit: only continue if last command succeeded
                            exit_code = shp.env.get("?", 0)
                            if exit_code != 0:
                                break
                        elif operator_text == "||":
                            # Short-circuit: only continue if last command failed
                            exit_code = shp.env.get("?", 0)
                            if exit_code == 0:
                                break
                        # For ";" or newline, just continue to next statement

            i += 1

    def visit_negated_command(self, node: ts.Node):
        child_nodes = [c for c in node.children if c.is_named]
        if not child_nodes:
            raise ValueError("Negated command has no child")

        # Build a runnable from the child and negate it
        runnable = self._node_to_runnable(child_nodes[0])
        runnable.negated()()

    def visit_pipeline(self, node: ts.Node):
        # Build and execute the pipeline using the helper method
        runnable = self._build_pipeline_runnable(node)
        runnable()

    def visit_redirected_statement(self, node: ts.Node):
        # Get the body (the command being redirected)
        body_node = node.child_by_field_name("body")
        if body_node is None:
            raise ValueError("Redirected statement has no body")

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
            shp.env["?"] = 0
            return

        # Get ALL current variables (not just exported ones)
        all_vars = dict(shp.env.items())

        # Execute bash with all variables passed via with_env
        # This uses the new fork-safe ExecutionContext architecture
        shp.prog("bash")("-c", commands_text).with_env(**all_vars)()

    def visit_test_command(self, node: ts.Node):
        """Execute a test command: [[ expression ]] or [ expression ]"""
        child = next((c for c in node.children if c.is_named), None)
        if child is None:
            # Empty test - should fail
            shp.env["?"] = 1
            return

        # Evaluate the expression
        result = self._evaluate_test_expression(child)

        # Set exit code: 0 for true, 1 for false
        shp.env["?"] = 0 if result else 1

    def _build_test_runnable_from_expression(self, node: ts.Node) -> shp.ShipRunnable:
        """Build a test runnable from a test expression node without executing it."""
        match node.type:
            case "binary_expression":
                return self._build_binary_test_runnable(node)
            case "unary_expression":
                return self._build_unary_test_runnable(node)
            case "parenthesized_expression":
                # Unwrap parentheses and build inner runnable
                inner = next((c for c in node.children if c.is_named), None)
                if inner is None:
                    return shp.prog("false")()
                return self._build_test_runnable_from_expression(inner)
            case _:
                # For words/strings: non-empty = true
                val = self._expand_primary_expression(node)
                if len(val) > 0:
                    return shp.prog("test")("-n", val)
                else:
                    return shp.prog("false")()

    def _evaluate_test_expression(self, node: ts.Node) -> bool:
        """Recursively evaluate a test expression to a boolean."""
        match node.type:
            case "binary_expression":
                return self._eval_binary_test(node)
            case "unary_expression":
                return self._eval_unary_test(node)
            case "parenthesized_expression":
                # Unwrap parentheses and evaluate inner expression
                inner = next((c for c in node.children if c.is_named), None)
                if inner is None:
                    return False
                return self._evaluate_test_expression(inner)
            case _:
                # For words/strings: non-empty = true
                val = self._expand_primary_expression(node)
                return len(val) > 0

    def _build_binary_test_runnable(self, node: ts.Node) -> shp.ShipRunnable:
        """Build a runnable for binary test expressions."""
        left = node.child_by_field_name("left")
        operator = node.child_by_field_name("operator")
        right_nodes = node.children_by_field_name("right")

        if left is None or operator is None or not right_nodes:
            return shp.prog("false")()

        op_text = self._get_text(operator)

        # Bash-specific operators that can't be delegated to test command
        if op_text == "=~":
            # Regex - evaluate in Python, return true/false
            import re

            left_val = self._expand_primary_expression(left)
            right_val = self._expand_primary_expression(right_nodes[0])
            try:
                result = re.search(right_val, left_val) is not None
                return shp.prog("true")() if result else shp.prog("false")()
            except re.error:
                return shp.prog("false")()

        # Logical operators - evaluate recursively
        elif op_text in ("&&", "-a"):
            # Build runnables for left and all rights, chain with &&
            # For now, evaluate to get result (can't easily chain with &&)
            left_result = self._evaluate_test_expression(left)
            if not left_result:
                return shp.prog("false")()
            all_right = all(self._evaluate_test_expression(r) for r in right_nodes)
            return shp.prog("true")() if all_right else shp.prog("false")()

        elif op_text in ("||", "-o"):
            # Similar  for ||
            left_result = self._evaluate_test_expression(left)
            if left_result:
                return shp.prog("true")()
            any_right = any(self._evaluate_test_expression(r) for r in right_nodes)
            return shp.prog("true")() if any_right else shp.prog("false")()

        # Standard operators: build test command
        else:
            left_val = self._expand_primary_expression(left)
            right_val = self._expand_primary_expression(right_nodes[0])

            # Translate == to = for POSIX test
            if op_text == "==":
                op_text = "="

            return shp.prog("test")(left_val, op_text, right_val)

    def _eval_binary_test(self, node: ts.Node) -> bool:
        """Evaluate binary test expressions."""
        # Build the runnable and execute it
        runnable = self._build_binary_test_runnable(node)
        runnable()
        return shp.env.get("?", 1) == 0

    def _build_unary_test_runnable(self, node: ts.Node) -> shp.ShipRunnable:
        """Build a runnable for unary test expressions."""
        operator = node.child_by_field_name("operator")
        if operator is None:
            return shp.prog("false")()

        operand = next((c for c in node.children if c != operator and c.is_named), None)
        if operand is None:
            return shp.prog("false")()

        op_text = self._get_text(operator)

        # Negation - evaluate in Python and return true/false
        if op_text == "!":
            result = self._evaluate_test_expression(operand)
            return shp.prog("true")() if not result else shp.prog("false")()

        # File/string tests - build test command
        else:
            operand_val = self._expand_primary_expression(operand)
            return shp.prog("test")(op_text, operand_val)

    def _eval_unary_test(self, node: ts.Node) -> bool:
        """Evaluate unary test expressions."""
        # Build the runnable and execute it
        runnable = self._build_unary_test_runnable(node)
        runnable()
        return shp.env.get("?", 1) == 0

    def visit_unset_command(self, node: ts.Node):
        """Handle unset command to remove variables"""
        # unset command removes variables from the environment
        # Usage: unset VAR1 VAR2 VAR3
        for child in node.children:
            if child.type in ("word", "variable_name"):
                var_name = self._get_text(child)
                # Use del to remove from environment (calls __delitem__)
                if var_name in shp.env:
                    del shp.env[var_name]

    def visit_variable_assignment(self, node: ts.Node):
        """Handle variable assignment: var=value"""
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")

        if name_node is None or value_node is None:
            raise ValueError("Invalid variable assignment")

        name = self._get_text(name_node)
        value = self._expand_primary_expression(value_node)

        # Set in shell environment (not exported by default)
        shp.env[name] = value

    def visit_while_statement(self, node: ts.Node):
        # Loop while condition is true (exit code 0)
        while True:
            # Execute condition statements
            condition_nodes = node.children_by_field_name("condition")
            for cond_node in condition_nodes:
                self.visit(cond_node)

            # Check exit code
            exit_code = shp.env.get("?", 0)

            # If condition failed, exit loop
            if exit_code != 0:
                break

            # Execute body (do_group)
            body_node = node.child_by_field_name("body")
            if body_node:
                self.visit_children(body_node)


def run_bash_code(
    bash_code: str, args: list[str] | None = None, script_name: str = "bash"
):
    """Parse and execute bash code using the ShipBashInterpreter.

    Args:
        bash_code: A string containing bash code to parse and execute
        args: Optional list of positional parameters ($1, $2, etc.)
        script_name: Name of the script ($0), defaults to "bash"
    """
    # Create the bash language and parser
    bash_language = Language(tsbash.language())
    parser = Parser(bash_language)

    # Parse the bash code
    tree = parser.parse(bytes(bash_code, "utf-8"))

    # Create interpreter and execute
    interpreter = ShipBashInterpreter(bash_code, args, script_name)
    interpreter.visit(tree.root_node)
