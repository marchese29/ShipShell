# IDE Support for ShipShell Scripts

ShipShell provides the `shp.ergo` package for IDE support when editing ShipShell scripts outside the REPL environment.

## The Problem

When writing ShipShell scripts in an IDE (VSCode, PyCharm, etc.), the IDE doesn't know about ShipShell's built-in functions like `cd()`, `pwd()`, or the shell functionality like `prog()`, `env`, etc. This results in:

- Red squiggly lines under function calls
- No autocomplete
- No type hints
- Missing function documentation

## The Solution

Use the `shp.ergo` package with the `ship_shell_marker` detection pattern:

```python
# At the top of your ShipShell script
try:
    import ship_shell_marker  # This module only exists in ShipShell
except ImportError:
    from shp.ergo import *  # Import for IDE support when editing
    from shp import prog, env  # Import shell primitives

# Now use ShipShell functions with full IDE support
cd("/tmp")
prog("ls")("-la")
env["MY_VAR"] = "hello"
```

## How It Works

### In the ShipShell REPL:
1. `ship_shell_marker` module is embedded and registered in `sys.modules`
2. The `try` block succeeds (`import ship_shell_marker` works)
3. The `except` block is skipped
4. You use the real ShipShell functions from the REPL environment

### In Your IDE (outside ShipShell):
1. `ship_shell_marker` module doesn't exist
2. The `try` block raises `ImportError`
3. The `except` block runs: `from shp.ergo import *` and `from shp import prog, env`
4. You get type hints and working implementations for IDE use

## What `shp.ergo` Provides

### Shell Built-ins (with real implementations):
- `cd(path)` - Change directory
- `pwd()` - Print working directory
- `pushd(path)` - Push directory onto stack
- `popd()` - Pop directory from stack
- `dirs()` - Display directory stack
- `source(filename)` - Execute Python file
- `exit(code)` / `quit(code)` - Exit shell

### Shell Functions (from `shp` module):
- `prog(name)` - Create program reference
- `cmd(prog, *args)` - Create command
- `pipe(cmd1, cmd2, ...)` - Create pipeline
- `sub(runnable)` - Create subshell
- `env` - Environment dictionary
- `get_env(key)` / `set_env(key, value)` - Environment access

### Type Hints:
All functions include full type annotations for IDE autocomplete and type checking.

## Example Script

Here's a complete example of a ShipShell script with IDE support:

```python
#!/usr/bin/env ship_shell
"""
Example ShipShell script with IDE support.
"""

# IDE support pattern
try:
    import ship_shell_marker
except ImportError:
    from shp.ergo import *
    from shp import prog, env
    import os

# Now write your script with full IDE support!

def backup_directory(source: str, dest: str) -> None:
    """Backup a directory using tar."""
    print(f"Backing up {source} to {dest}")
    
    # Change to source directory
    original = os.getcwd()
    cd(source)
    
    # Create tar archive
    prog("tar")("-czf", dest, ".")()
    
    # Return to original directory
    cd(original)

# Main script
if __name__ == "__main__":
    pwd()  # Shows current directory
    
    # Set environment variable
    env["BACKUP_DIR"] = "/tmp/backups"
    
    # Run commands
    prog("mkdir")("-p", env["BACKUP_DIR"])()
    backup_directory("~/Documents", f"{env['BACKUP_DIR']}/docs.tar.gz")
    
    print("Backup complete!")
```

## Benefits

✅ **Full type hints** - `Optional[str]`, return types, etc.  
✅ **Autocomplete** - IDEs can suggest functions and their parameters  
✅ **Documentation** - Hover over functions to see docstrings  
✅ **Real implementations** - Built-ins actually work outside ShipShell  
✅ **Separate packages** - `shp` for shell primitives, `shp.ergo` for builtins  
✅ **Clean REPL** - No typing imports pollute ShipShell namespace  

## Running Scripts

### In ShipShell REPL:
```python
ship> source("my_script.py")
```

### Outside ShipShell (for testing):
```bash
python my_script.py
```

Note: Shell commands (`prog()`, etc.) will raise `NotImplementedError` outside ShipShell, but built-ins like `cd()` will work!

## Advanced Usage

You can also import specific functions if you don't want the entire namespace:

```python
try:
    import ship_shell_marker
except ImportError:
    from shp.ergo import cd, pwd
    from shp import prog, env

# Use only imported functions
cd("/tmp")
pwd()
```

## Package Structure

- **`shp`** - Core shell API (prog, env, cmd, pipe, sub, etc.)
  - In ShipShell: Rust-native implementations
  - Externally: Python stubs for IDE support
- **`shp.ergo`** - Ergonomic shell built-ins (cd, pwd, pushd, etc.)
  - Work both in ShipShell and standalone Python
  - Provide rich type hints and documentation
