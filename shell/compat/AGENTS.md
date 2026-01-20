# Bash Compatibility Layer

## Architecture

The bash interpreter uses tree-sitter-bash to parse bash code into a CST, then walks the tree to execute commands.

### Key Components

- `ShipBashInterpreter` - Main interpreter class with visitor pattern
- `run_bash_code(code, env=global_env)` - Entry point to execute bash code
- `print_bash_tree(code)` - Debug utility to visualize the parse tree

### Visitor Pattern

Methods follow naming conventions:
- `visit_*` - Execute node with side effects (commands, assignments)
- `evaluate_*` - Return a BashValue (expansions, expressions)

```python
def visit_command(self, node):
    """Execute a command - has side effects."""
    runnable = self._build_command_runnable(node)
    runnable()

def evaluate_expansion(self, node):
    """Evaluate ${...} - returns a value."""
    return expanded_value
```

### Pure Functions

These have no side effects and are unit tested in `tests/test_bash_pure.py`:

- `_bash_to_str(value)` - Convert BashValue to string
- `_bash_to_int(value)` - Convert BashValue to int
- `_bash_to_file(value)` - Convert BashValue to file descriptor/path
- `_expand_range(content)` - Expand `{1..10}` style ranges
- `_split_commas(string)` - Split on commas at depth 0
- `_expand_braces(string)` - Full brace expansion

## Debugging

### Print the Parse Tree

```python
from shell.compat.bash import print_bash_tree

print_bash_tree('echo "hello $NAME"')
# program
#   command
#     command_name
#       word ['echo']
#     string
#       string_content ['hello ']
#       simple_expansion
#         variable_name ['NAME']
```

### Test Against Real Bash

```python
from tests.bash import run_isolated, run_bash_reference

# Compare our output to real bash
ours = run_isolated('echo {a,b,c}')
bash = run_bash_reference('echo {a,b,c}')

print(f"Ours: {ours.stdout!r}")
print(f"Bash: {bash.stdout!r}")
```

### Interactive Testing

```python
from shell.environment import env
from shell.compat.bash import run_bash_code

env.initialize()
run_bash_code('export FOO=bar; echo $FOO', env=env)
print(f"FOO = {env.get('FOO')}")
print(f"Exit code = {env.last_exit}")
```

## Common Patterns

### Adding a New Node Type

1. Check the node structure with `print_bash_tree()`
2. Add `visit_*` or `evaluate_*` method
3. Add integration test in `tests/test_bash_compat.py`

### Handling Variable Names

Use `evaluate()` which dispatches to `evaluate_variable_name()`:

```python
name = _bash_to_str(self.evaluate(name_node))  # Correct
name = self._get_text(name_node)                # Also works for simple cases
```

### Exit Codes

Exit codes are captured via `env.last_exit` after commands run:

```python
from shell.model import run
result = some_runnable()  # run() sets env.last_exit automatically
```
