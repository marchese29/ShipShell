# Tab Completion with Jedi

ShipShell's REPL includes intelligent tab completion powered by the [jedi](https://github.com/davidhalter/jedi) library. This provides context-aware Python code suggestions as you type.

## Features

### Intelligent Python Completions

The tab completion system understands Python syntax and provides completions for:

- **Variables**: Complete variable names from the current REPL namespace
- **Attributes and Methods**: Complete object attributes and methods with type information
- **Module Names**: Complete module names for import statements
- **Built-in Functions**: Complete Python built-ins and standard library functions
- **Function Parameters**: Shows function signatures and parameter hints

### Context-Aware Suggestions

Jedi analyzes the code context to provide relevant suggestions:

```python
ship> my_list = [1, 2, 3, 4, 5]
ship> my_list.ap<TAB>       # Suggests: append
ship> my_list.append(6)
ship> my_list
[1, 2, 3, 4, 5, 6]

ship> import o<TAB>         # Suggests: os, operator, optparse, etc.
ship> import os
ship> os.pat<TAB>           # Suggests: os.path, os.pathconf, etc.
ship> os.path.join("/tmp", "file.txt")
'/tmp/file.txt'
```

### Type Information

Completions include type annotations to help you understand what you're completing:

- `(function)` - Functions and methods
- `(instance)` - Variables and attributes
- `(module)` - Importable modules
- `(class)` - Class definitions
- `(param)` - Function parameters

## Usage

### Basic Tab Completion

Press the `TAB` key while typing to trigger completions:

```python
ship> import sys
ship> sys.ver<TAB>
# Shows dropdown with:
#   version (instance)
#   version_info (instance)
```

### Navigation

- **TAB**: Trigger or cycle through completions
- **Arrow Keys**: Navigate the completion menu
- **ENTER**: Accept the selected completion
- **ESC**: Cancel completion and return to editing

### Multi-Level Completions

Tab completion works across multiple levels of attribute access:

```python
ship> import os.path
ship> os.path.split<TAB>
# Shows:
#   split (function)
#   splitdrive (function)
#   splitext (function)
```

### ShipShell-Specific Completions

The completer has access to all ShipShell built-ins and the `shp` module:

```python
ship> import shp
ship> shp.prog<TAB>         # Completes to: shp.prog
ship> shp.prog("ls")
<ShipProgram 'ls'>
```

## Implementation Details

### Architecture

The tab completion is implemented using:

1. **jedi.Interpreter**: Analyzes Python code in REPL context
2. **prompt_toolkit.Completer**: Integrates with the prompt_toolkit input system
3. **JediCompleter class**: Custom completer that bridges jedi and prompt_toolkit

### Namespace Integration

The completer has full access to the REPL's namespace, including:

- All defined variables and functions
- Imported modules and their contents
- ShipShell built-ins (`shp`, `prog`, `cmd`, etc.)
- Standard library modules

### Error Handling

The completion system is designed to never interrupt your workflow:

- Syntax errors in incomplete code are silently handled
- Failed completions don't break the REPL
- Jedi exceptions are caught and logged without disrupting input

### Performance

- Completions are generated on-demand (only when TAB is pressed)
- Jedi uses intelligent caching to minimize analysis overhead
- The completion system is non-blocking and responsive

## Configuration

### Automatic Installation

The jedi library is automatically installed when the REPL starts:

```python
# In python/shell/repl.py
from shp.py_env import install_packages
install_packages("prompt_toolkit", "jedi")
```

### Customization

You can customize completion behavior by modifying the `JediCompleter` class in `python/shell/repl.py`:

```python
class JediCompleter(Completer):
    def __init__(self, namespace: dict):
        self.namespace = namespace
        # Add custom configuration here
    
    def get_completions(self, document, complete_event):
        # Modify completion logic here
        ...
```

## Examples

### Basic Variable Completion

```python
ship> my_variable = "hello world"
ship> my_v<TAB>              # Completes to: my_variable
ship> my_variable.upper()
'HELLO WORLD'
```

### Method Completion with Type Hints

```python
ship> numbers = [1, 2, 3, 4, 5]
ship> numbers.<TAB>
# Shows all list methods with (function) type:
#   append (function)
#   clear (function)
#   copy (function)
#   count (function)
#   extend (function)
#   index (function)
#   insert (function)
#   pop (function)
#   remove (function)
#   reverse (function)
#   sort (function)
```

### Import Statement Completion

```python
ship> import os<TAB>
# Shows:
#   os (module)
#   ossaudiodev (module)

ship> from os import pat<TAB>
# Shows:
#   path (module)
#   pathconf (function)
#   pathconf_names (instance)
```

### Chained Attribute Completion

```python
ship> import datetime
ship> now = datetime.datetime.now()
ship> now.isocal<TAB>        # Completes to: now.isocalendar
ship> now.isocalendar()
datetime.IsoCalendarDate(year=2024, week=47, weekday=5)
```

### Function Signature Help

```python
ship> def greet(name, greeting="Hello"):
...     return f"{greeting}, {name}!"
ship> greet<TAB>
# Shows: greet (function)
# When you type '(' you may see parameter hints depending on configuration
```

## Troubleshooting

### Completions Not Appearing

If tab completion doesn't work:

1. **Check jedi installation**: The first REPL start installs jedi automatically
2. **Verify syntax**: Completions only work on valid Python syntax up to the cursor
3. **Check namespace**: Variables must be defined in the current session

### Slow Completions

If completions are slow:

1. **Large modules**: Importing large modules (e.g., numpy) may slow initial completions
2. **Complex code**: Deeply nested completions may take longer to analyze
3. **System resources**: Jedi uses CPU for code analysis; ensure adequate resources

### Wrong Suggestions

If completions seem incorrect:

1. **Type inference**: Jedi infers types; dynamic code may confuse it
2. **Namespace updates**: Ensure variables are properly defined in the REPL
3. **Module reloading**: Reloaded modules may not update immediately

## Technical Details

### Code Structure

The tab completion implementation consists of:

**`python/shell/repl.py`**: Main REPL implementation
- `JediCompleter` class: Custom completer for jedi integration
- `run_repl()` function: Initializes completer and attaches to PromptSession

**Key Components**:
```python
class JediCompleter(Completer):
    """Custom completer using jedi for intelligent completions."""
    
    def __init__(self, namespace: dict):
        self.namespace = namespace
    
    def get_completions(self, document, complete_event):
        """Generate completions using jedi.Interpreter."""
        import jedi
        
        text = document.text_before_cursor
        script = jedi.Interpreter(text, namespaces=[self.namespace])
        completions = script.complete()
        
        for completion in completions:
            yield Completion(
                completion.name,
                start_position=-len(completion.complete),
                display=completion.name,
                display_meta=f"({completion.type})"
            )
```

### Jedi Integration

The implementation uses `jedi.Interpreter` instead of `jedi.Script` because:

1. **REPL Context**: Interpreter understands interactive Python sessions
2. **Namespace Access**: Can directly access the REPL's namespace
3. **Dynamic Evaluation**: Better handles dynamically created variables

### Prompt Toolkit Integration

The completer integrates with prompt_toolkit's completion system:

1. **Completer Interface**: Implements `get_completions()` method
2. **Document API**: Uses prompt_toolkit's Document for cursor position
3. **Completion Objects**: Returns prompt_toolkit Completion instances

## Related Documentation

- [Jedi Documentation](https://jedi.readthedocs.io/)
- [Prompt Toolkit Documentation](https://python-prompt-toolkit.readthedocs.io/)
- [ShipShell REPL Documentation](./REPL.md)

## Future Enhancements

Possible improvements to the completion system:

- **Signature help**: Show function signatures in a hover popup
- **Documentation hints**: Display docstrings alongside completions
- **Fuzzy matching**: Support fuzzy/approximate completion matching
- **Custom completers**: Allow users to register custom completion providers
- **Completion filtering**: Filter by type (only functions, only variables, etc.)
- **Performance tuning**: Cache frequently accessed completions
- **Multi-line context**: Better handle completions in multi-line statements
