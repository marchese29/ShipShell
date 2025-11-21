# Tab Completion Quick Reference

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `TAB` | Trigger/cycle completions |
| `↑` `↓` | Navigate completion menu |
| `ENTER` | Accept selected completion |
| `ESC` | Cancel completion |

## Completion Types

### Variables
```python
ship> my_variable = 42
ship> my_<TAB>              # ✓ Completes variable names
```

### Attributes & Methods
```python
ship> [1,2,3].<TAB>         # ✓ Shows list methods
ship> "text".<TAB>          # ✓ Shows string methods
ship> obj.attr.<TAB>        # ✓ Chained completion
```

### Modules & Imports
```python
ship> import o<TAB>         # ✓ Shows modules (os, operator, etc.)
ship> from os import p<TAB> # ✓ Shows os.path, etc.
ship> os.path.<TAB>         # ✓ Shows path functions
```

### Built-ins
```python
ship> pri<TAB>              # ✓ Completes to: print
ship> ra<TAB>               # ✓ Shows: range, raise, etc.
```

### ShipShell Commands
```python
ship> import shp
ship> shp.<TAB>             # ✓ Shows: prog, cmd, capture, etc.
ship> shp.prog<TAB>         # ✓ Completes to: shp.prog
```

## Type Information

Completions show type in parentheses:
- `(function)` - Functions and methods
- `(instance)` - Variables and attributes
- `(module)` - Python modules
- `(class)` - Class definitions
- `(param)` - Function parameters

## Tips & Tricks

### 1. Narrow Down Results
Type more characters to filter completions:
```python
ship> import os
ship> os.p<TAB>             # Many results
ship> os.pat<TAB>           # Fewer results: path, pathconf
ship> os.path<TAB>          # Just: os.path
```

### 2. Explore Objects
Use completion to discover available methods:
```python
ship> x = [1, 2, 3]
ship> x.<TAB>               # See all list methods
```

### 3. Learn APIs
Check what's available in modules:
```python
ship> import datetime
ship> datetime.<TAB>        # Browse datetime API
```

### 4. Fix Typos
Use completion to avoid misspelling:
```python
ship> import sys
ship> sys.ver<TAB>          # Shows: version, version_info
```

### 5. Speed Up Coding
Let completion do the typing:
```python
ship> my_really_long_variable_name = 100
ship> my_r<TAB>             # Auto-completes full name
```

## Common Patterns

### Working with Files
```python
ship> import os.path
ship> os.path.j<TAB>        # join
ship> os.path.join("/home", "user")
```

### String Manipulation
```python
ship> text = "Hello World"
ship> text.lo<TAB>          # lower
ship> text.sp<TAB>          # split
```

### List Operations
```python
ship> items = [3, 1, 4, 1, 5]
ship> items.so<TAB>         # sort
ship> items.ap<TAB>         # append
```

### Dictionary Access
```python
ship> data = {"key": "value"}
ship> data.g<TAB>           # get
ship> data.k<TAB>           # keys
```

## Troubleshooting

### No Completions Appearing?

**Check syntax**: Completion requires valid Python up to cursor
```python
ship> def func(<TAB>        # ✗ Invalid syntax
ship> my_var<TAB>           # ✓ Valid
```

**Check namespace**: Variable must be defined
```python
ship> undefined_var.<TAB>   # ✗ Not defined
ship> x = 5
ship> x.<TAB>               # ✓ Defined
```

### Wrong Suggestions?

**Type more characters** to narrow results:
```python
ship> import os
ship> os.p<TAB>             # Too many results
ship> os.path.<TAB>         # Better targeting
```

### Slow Completions?

**First completion may be slower** for large modules:
```python
ship> import numpy         # Initial import slow
ship> numpy.<TAB>          # First completion may lag
ship> numpy.a<TAB>         # Subsequent faster (cached)
```

## Examples by Use Case

### Data Analysis
```python
ship> import pandas as pd
ship> df = pd.DataFrame({'A': [1,2,3]})
ship> df.<TAB>              # Explore DataFrame methods
```

### File Operations
```python
ship> import pathlib
ship> p = pathlib.Path(".")
ship> p.<TAB>               # See Path methods
```

### System Commands (ShipShell)
```python
ship> import shp
ship> result = shp.prog("ls")("-la")
ship> result.<TAB>          # See result attributes
```

### Web Development
```python
ship> import urllib.request
ship> urllib.request.<TAB>  # Browse urllib functions
```

## Learn More

- **Full Documentation**: [docs/TAB_COMPLETION.md](TAB_COMPLETION.md)
- **Demo Script**: [examples/tab_completion_demo.py](../examples/tab_completion_demo.py)
- **Interactive Practice**: Start ShipShell REPL and experiment!

## Remember

✨ **Tab completion makes coding faster and more enjoyable!**

- Press TAB often while coding
- Use it to explore new modules
- Let it help you avoid typos
- Discover new methods and functions
- Speed up your workflow

Happy coding! 🚀
