# Shell Built-ins for ShipShell (macOS)

This document categorizes common shell built-in commands and explains how they are handled in ShipShell.

## Category 1: Use External Executables (Already on PATH)

These built-ins exist as standalone executables in macOS and can be used via `prog()`:

- **echo** - `/bin/echo` (though `print()` is recommended)
- **pwd** - `/bin/pwd` (though the `pwd()` function is more convenient)
- **test** / **[** - `/bin/test` (for file/string tests)
- **printf** - `/usr/bin/printf`
- **kill** - `/bin/kill` (for signaling processes)
- **sleep** - `/bin/sleep`
- **true** / **false** - `/usr/bin/true`, `/usr/bin/false`

**Usage example:**
```python
prog("echo")("Hello, World!")
prog("sleep")("2")
```

## Category 2: Implemented as Python Functions

These functions require access to the shell's state or provide essential shell functionality. They are implemented in `python/init.py`:

### **cd(path=None)**
Change the current working directory. If no path is provided, changes to the home directory.

```python
cd("/tmp")
cd("~/Documents")
cd()  # Go home
```

### **source(filename)**
Execute a Python file in the current namespace. Useful for loading configuration or reusable functions.

```python
source("~/.shipshellrc")
source("my_aliases.py")
```

**Note:** In sourced files, ShipRunnable objects do NOT auto-execute. You must explicitly call them:
```python
# Inside a sourced file:
prog("ls")("-la")()  # Note the extra () to execute
```

### **exit(code=0)** and **quit(code=0)**
Exit the shell with an optional exit code.

```python
exit()
quit(1)
```

### **pushd(path)**, **popd()**, **dirs()**
Directory stack navigation for quickly moving between directories.

```python
pushd("/tmp")        # Save current dir, cd to /tmp
pushd("/var/log")    # Save /tmp, cd to /var/log
dirs()               # Show stack: /var/log /tmp /original/dir
popd()               # Return to /tmp
popd()               # Return to /original/dir
```

### **pwd()**
Print the current working directory (convenience wrapper around `os.getcwd()`).

```python
pwd()
```

## Category 3: Python Stand-ins (No Implementation Needed)

Python's built-in features already provide equivalent or superior functionality:

| Shell Built-in | Python Equivalent | Notes |
|---------------|-------------------|-------|
| **echo** | `print()` | More powerful: formatting, multiple args, etc. |
| **pwd** | `os.getcwd()` | Also available as `pwd()` function for convenience |
| **export** | `env['VAR'] = value` | Use ShipEnv dictionary |
| **unset** | `del env['VAR']` | Use ShipEnv dictionary |
| **test** | `os.path.*` functions | `os.path.exists()`, `os.path.isfile()`, etc. |
| **read** | `input()` | Python built-in |
| **eval** | `eval()` / `exec()` | Python built-ins |
| **alias** | Python functions | Define functions or lambdas |
| **set** | Variable assignment | Native Python syntax |
| **return** | `return` statement | Python control flow |
| **for/while/if** | Python control structures | Native Python syntax |

**Usage examples:**
```python
# Environment variables
env['MY_VAR'] = "hello"
print(env['MY_VAR'])
del env['MY_VAR']

# File tests
import os
if os.path.exists("file.txt"):
    print("File exists!")

# Aliases (just use functions)
ll = lambda: prog("ls")("-la")
ll()  # Executes ls -la

# Input
name = input("Enter your name: ")
```

## Design Notes

### Auto-execution Behavior

ShipShell automatically executes `ShipRunnable` objects when they are the **result of a REPL expression**:

```python
# REPL: Auto-executes
prog("ls")("-la")

# Assignment: Doesn't execute (stored for later)
my_cmd = prog("ls")("-la")
my_cmd()  # Execute explicitly

# Inside functions/scripts: Must call explicitly
if some_condition:
    prog("pwd")()  # Need the () to execute
```

This design keeps the REPL convenient while ensuring scripts are explicit about execution.

### Import Strategy

All imports in `init.py` are kept inside functions to avoid polluting the global namespace. Users can import modules as needed in their own REPL sessions.
