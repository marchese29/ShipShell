"""
Compatibility layer for running non-Python shell scripts.
"""

import tree_sitter_bash as tsbash
from tree_sitter import Language, Parser

from .bash import ShipBashInterpreter


def run_bash_code(bash_code: str):
    """
    Parse and execute bash code using the ShipBashInterpreter.

    This function allows you to run bash scripts within ShipShell, translating
    bash syntax to ShipShell commands. Useful for running existing bash scripts
    or when you prefer bash syntax for certain operations.

    Args:
        bash_code: A string containing bash code to parse and execute

    Example:
        run_bash_code('echo "Hello from bash!"')
        run_bash_code('''
            if true; then
                echo "Condition passed"
            fi
        ''')
        run_bash_code('echo "Step 1" && echo "Step 2" && echo "Step 3"')
    """
    # Create the bash language and parser
    bash_language = Language(tsbash.language())
    parser = Parser(bash_language)

    # Parse the bash code
    tree = parser.parse(bytes(bash_code, 'utf-8'))

    # Create interpreter and execute
    interpreter = ShipBashInterpreter(bash_code)
    interpreter.visit(tree.root_node)
