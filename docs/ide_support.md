# IDE Support for ShipShell Scripts

ShipShell provides the `ship_ergo` package for IDE support when editing ShipShell scripts outside the REPL environment.

## The Problem

When writing ShipShell scripts in an IDE (VSCode, PyCharm, etc.), the IDE doesn't know about ShipShell's built-in functions like `cd()`, `pwd()`, or the shell functionality like `prog()`, `cmd()`, etc. This results in:

- Red squiggly lines under function calls
- No autocomplete
- No type hints
- Missing function documentation

## The Solution

Use the `ship_ergo` package with the `ship_shell_marker` detection pattern:

```python
# At the top of your ShipShell script
try:
    import ship_shell_marker  # This module only exists in ShipShell
except ImportError:
    from ship_ergo import *  # Import for IDE support when editing

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
3. The `except` block runs: `from ship_ergo import *`
4. You get type hints and working implementations for IDE use

## What `ship_ergo` Provides

### Shell Built-ins (with real implementations):
- `cd(path)` - Change directory
- `pwd()` - Print working directory
- `pushd(path)` - Push directory onto stack
- `popd()` - Pop directory from stack
- `dirs()` - Display directory stack
- `source(filename)` - Execute Python file
- `exit(code)` / `quit(code)` - Exit shell

### Shell Functions:
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
    from ship_ergo import *

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
✅ **Single source** - Functions defined once in `ship_ergo.builtins`  
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

Note: Shell commands (`prog()`, `cmd()`, etc.) will raise `NotImplementedError` outside ShipShell, but built-ins like `cd()` will work!

## Advanced Usage

You can also import specific functions if you don't want the entire namespace:

```python
try:
    IN_SHIP_SHELL
except NameError:
    from ship_ergo import cd, pwd, prog, env

# Use only imported functions
cd("/tmp")
pwd()
```

Or check the flag explicitly:

```python
try:
    IN_SHIP_SHELL
except NameError:
    from ship_ergo import *
    IN_SHIP_SHELL = False

# Now you can check the flag
if IN_SHIP_SHELL:
    # Only run in actual ShipShell
    prog("some-shell-specific-command")()
else:
    # Fallback for regular Python
    import subprocess
    subprocess.run(["some-shell-specific-command"])
```
