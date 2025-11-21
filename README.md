# ShipShell

A shell environment using Python

## Name

The name "ShipShell" comes from **shell-python** → sh-p → sounds like "ship" → ShipShell.

## Overview

ShipShell is a Python REPL (Read-Eval-Print Loop) built in Rust using PyO3. It provides an interactive Python environment with custom bindings and shell-like functionality.

## Features

### Intelligent Tab Completion

ShipShell includes powerful tab completion powered by the [jedi](https://github.com/davidhalter/jedi) library:

- **Context-aware completions**: Intelligent suggestions based on Python code analysis
- **Variable and attribute completion**: Complete variable names, object attributes, and methods
- **Module and import completion**: Smart suggestions for module names and imports
- **Type information**: See the type of each suggestion (function, instance, module, etc.)
- **Works with ShipShell built-ins**: Full support for `shp` module and shell commands

Simply press `TAB` while typing to trigger completions. See [docs/TAB_COMPLETION.md](docs/TAB_COMPLETION.md) for detailed documentation.

### REPL Hooks

- **Before Prompt**: Execute code before showing the primary prompt
- **Before Continuation**: Run callbacks during multi-line input
- **Before/After Execute**: Hook into command execution lifecycle

### Shell Integration

- Custom command execution via the `shp` module
- Environment variable management
- Pipeline and command composition support
