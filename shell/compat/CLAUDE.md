# Bash Compatibility Layer

This module provides bash syntax support for ShipShell. It's a **feature** of the REPL, not the core - the core is `shell/model.py` which provides the Pythonic shell abstractions that the bash interpreter builds upon.

## Architecture

The bash interpreter uses tree-sitter-bash to parse bash code into a CST, then walks the tree to build `ShellRunnable` objects from `shell/model.py`.

### Key Components

- `BashInterpreter` - Main interpreter class with visitor pattern
- `run_bash_code(code, env=global_env)` - Entry point to execute bash code
- `print_bash_tree(code)` - Debug utility to visualize the parse tree

### Visitor Pattern

Methods follow naming conventions:
- `visit_*` - Build a ShellRunnable for the node (deferred execution)
- `evaluate_*` - Return a BashValue (expansions, expressions)
- `execute()` - Dispatch to visit_* and run the returned runnable

```python
def visit_command(self, node) -> ShellRunnable:
    """Build a runnable for command execution."""
    return prog(cmd_name)(*args)

def execute(self, node):
    """Build and run the runnable for a node."""
    runnable = self.visit(node)
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

### Shell Options

The interpreter tracks shell options in `self._shell_options`. Key options:

| Option | Flag | Effect |
|--------|------|--------|
| `errexit` | `-e` | Exit on command failure |
| `nounset` | `-u` | Error on unset variables |
| `xtrace` | `-x` | Print commands before execution |
| `pipefail` | | Pipeline fails if any stage fails |
| `errtrace` | `-E` | ERR trap inherited by functions/subshells |
| `functrace` | `-T` | DEBUG/RETURN traps inherited |

### Variable Attributes

Variable attributes are tracked using `VarAttr` IntFlag in a single dict, shared with parent interpreters:

```python
class VarAttr(IntFlag):
    NONE = 0
    READONLY = auto()   # -r
    ASSOC = auto()      # -A
    INTEGER = auto()    # -i
    LOWERCASE = auto()  # -l
    UPPERCASE = auto()  # -u

self._var_attrs: dict[str, VarAttr] = {}
```

| Flag | `VarAttr` | Effect |
|------|-----------|--------|
| `-r` | `READONLY` | Reject assignments, error on unset |
| `-A` | `ASSOC` | Treat as associative array (dict) |
| `-i` | `INTEGER` | Evaluate values as arithmetic on assignment |
| `-l` | `LOWERCASE` | Lowercase values on assignment |
| `-u` | `UPPERCASE` | Uppercase values on assignment |

**Adding a new attribute:**
1. Add value to `VarAttr` enum (e.g., `TRACE = auto()`)
2. Parse the flag in `visit_declaration_command` flag loop
3. Apply the effect in `_set_variable` (for assignment-time behavior)
4. Use bitwise ops: `self._var_attrs[name] = attrs | VarAttr.NEW_FLAG`

Case conversion (`-l`/`-u`) are mutually exclusive—use `(attrs | VarAttr.LOWERCASE) & ~VarAttr.UPPERCASE`.

### Process Substitution

`<(cmd)` and `>(cmd)` are handled by `evaluate_process_substitution()`, which uses `ProcessSubstitution` from `shell/model.py`. Process subs are tracked in `self._process_subs` and cleaned up in `execute()`.
