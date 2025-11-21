#!/usr/bin/env python3
"""
Tab Completion Demo for ShipShell REPL

This script demonstrates the tab completion features available in the ShipShell REPL.
To use this demo, start the ShipShell REPL and try the examples below.

Note: This is a reference file. The actual tab completion happens interactively
in the REPL when you press the TAB key.
"""

# =============================================================================
# Example 1: Variable Name Completion
# =============================================================================
print("Example 1: Variable Name Completion")
print("-" * 40)

# Define some variables
my_string = "Hello, World!"
my_list = [1, 2, 3, 4, 5]
my_dict = {"name": "ShipShell", "version": "0.1.0"}
my_number = 42

# Try typing: my_<TAB>
# This will show all variables starting with "my_":
#   - my_string
#   - my_list
#   - my_dict
#   - my_number


# =============================================================================
# Example 2: Method and Attribute Completion
# =============================================================================
print("\nExample 2: Method and Attribute Completion")
print("-" * 40)

# String methods
text = "hello world"
# Try typing: text.<TAB>
# This shows all string methods: upper, lower, capitalize, split, etc.

# Example: text.upper()  -> "HELLO WORLD"

# List methods
numbers = [3, 1, 4, 1, 5, 9, 2, 6]
# Try typing: numbers.<TAB>
# This shows all list methods: append, sort, reverse, pop, etc.

# Example: numbers.sort() -> [1, 1, 2, 3, 4, 5, 6, 9]


# =============================================================================
# Example 3: Module Import Completion
# =============================================================================
print("\nExample 3: Module Import Completion")
print("-" * 40)

# Try typing: import o<TAB>
# This shows modules starting with "o": os, operator, optparse, etc.

import os
import sys
import datetime

# Try typing: from os import pat<TAB>
# This shows: path, pathconf, pathconf_names


# =============================================================================
# Example 4: Module Attribute Completion
# =============================================================================
print("\nExample 4: Module Attribute Completion")
print("-" * 40)

import os

# Try typing: os.path.<TAB>
# This shows all os.path functions: join, split, exists, dirname, etc.

# Example: os.path.join("/home", "user", "file.txt")

# Try typing: sys.ver<TAB>
# This shows: version, version_info

# Example: sys.version_info


# =============================================================================
# Example 5: Chained Completion
# =============================================================================
print("\nExample 5: Chained Completion")
print("-" * 40)

import datetime

now = datetime.datetime.now()

# Try typing: now.<TAB>
# This shows all datetime methods: year, month, day, hour, isoformat, etc.

# Try typing: now.isoformat().<TAB>
# This shows string methods on the result

# Example: now.isoformat().upper()


# =============================================================================
# Example 6: Dictionary and Object Completion
# =============================================================================
print("\nExample 6: Dictionary and Object Completion")
print("-" * 40)

# Dictionary with string keys
config = {
    "host": "localhost",
    "port": 8080,
    "debug": True
}

# Try typing: config.<TAB>
# This shows dictionary methods: get, keys, values, items, etc.

# Example: config.get("host")  -> "localhost"

# Custom class
class Person:
    def __init__(self, name, age):
        self.name = name
        self.age = age
    
    def greet(self):
        return f"Hello, I'm {self.name}"

person = Person("Alice", 30)

# Try typing: person.<TAB>
# This shows: name, age, greet


# =============================================================================
# Example 7: ShipShell-Specific Completions
# =============================================================================
print("\nExample 7: ShipShell-Specific Completions")
print("-" * 40)

# The shp module provides ShipShell functionality
# Try typing: import shp
# Then try: shp.<TAB>
# This shows: prog, cmd, pipe, capture, get_stdout, env, etc.

# Example with ShipShell commands (when running in actual ShipShell):
# shp.prog("ls")("-la")()
# shp.get_stdout(shp.prog("echo")("Hello"))


# =============================================================================
# Example 8: Function Parameter Hints
# =============================================================================
print("\nExample 8: Function Definition Completion")
print("-" * 40)

def calculate_area(width, height, unit="meters"):
    """Calculate the area of a rectangle."""
    return width * height

def process_data(data, transform=None, validate=True):
    """Process data with optional transformation and validation."""
    if validate:
        # validation logic
        pass
    if transform:
        data = transform(data)
    return data

# Try typing: calculate_area<TAB>
# This shows: calculate_area (function)

# Try typing: process_data<TAB>
# This shows: process_data (function)


# =============================================================================
# Example 9: Nested Object Completion
# =============================================================================
print("\nExample 9: Nested Object Completion")
print("-" * 40)

class Database:
    class Connection:
        def connect(self):
            return "Connected"
        
        def disconnect(self):
            return "Disconnected"
    
    def __init__(self):
        self.connection = self.Connection()
        self.host = "localhost"
        self.port = 5432

db = Database()

# Try typing: db.<TAB>
# Shows: connection, host, port

# Try typing: db.connection.<TAB>
# Shows: connect, disconnect


# =============================================================================
# Example 10: Completion with Built-in Types
# =============================================================================
print("\nExample 10: Completion with Built-in Types")
print("-" * 40)

# Integer methods
num = 42
# Try typing: num.<TAB>
# Shows: bit_length, to_bytes, from_bytes, etc.

# String methods
text = "Python"
# Try typing: text.<TAB>
# Shows: upper, lower, capitalize, format, etc.

# List methods
items = [1, 2, 3]
# Try typing: items.<TAB>
# Shows: append, extend, insert, remove, pop, etc.

# Dict methods
data = {"key": "value"}
# Try typing: data.<TAB>
# Shows: get, keys, values, items, update, etc.


# =============================================================================
# Interactive Demo Instructions
# =============================================================================
print("\n" + "=" * 60)
print("INTERACTIVE DEMO INSTRUCTIONS")
print("=" * 60)
print("""
To test tab completion interactively:

1. Start the ShipShell REPL:
   $ cargo run

2. Type any of the examples above and press TAB where indicated

3. Observe the completion suggestions that appear

4. Use arrow keys to navigate suggestions

5. Press ENTER to accept or ESC to cancel

Tips:
- Tab completion works anywhere in your input
- It understands the context of what you're typing
- Type more characters to narrow down suggestions
- Completions include type information (function, instance, module, etc.)

Try it yourself:
  ship> import <TAB>           # Shows available modules
  ship> [1,2,3].<TAB>          # Shows list methods
  ship> "hello".<TAB>          # Shows string methods
  ship> import sys
  ship> sys.<TAB>              # Shows sys module contents
""")
