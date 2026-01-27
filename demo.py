#!/usr/bin/env python3
"""
ShipShell Demo: Python Meets Shell

Run with: uv run python demo.py

This demonstrates ShipShell's unique value - seamlessly mixing Python
logic with shell operations through the | operator.
"""

from shell.environment import env
from shell.model import prog, capture
import sys

env.initialize()

# Convenient aliases (normally done by wire_path_programs in the REPL)
git, grep, sort, uniq, head = (
    prog('git'), prog('grep'), prog('sort'), prog('uniq'), prog('head')
)

print("""
╔═════════════════════════════════════════════════════════════════╗
║             ShipShell: Python Meets Shell                       ║
╚═════════════════════════════════════════════════════════════════╝
""")

# -----------------------------------------------------------------------------
# 1. Python function IN a shell pipeline
# -----------------------------------------------------------------------------
print("▶ Python function in a shell pipeline:\n")
print("  git log | sort | uniq -c | sort -rn | <python formatter>\n")


def format_leaderboard():
    """Python function that reads stdin and formats output."""
    for i, line in enumerate(sys.stdin, 1):
        count, name = line.strip().split(maxsplit=1)
        bar = '▓' * min(int(count), 25)
        medal = ['🥇', '🥈', '🥉'][i - 1] if i <= 3 else '  '
        print(f"  {medal} {name:25} {bar} {count}")
        if i >= 5:
            break


(git('log', '--format=%aN') | sort | uniq('-c') | sort('-rn') | format_leaderboard)()

# -----------------------------------------------------------------------------
# 2. stdin_content - pipe Python data directly to commands
# -----------------------------------------------------------------------------
print("\n▶ Pipe Python data directly to shell commands:\n")
print("  grep('ERROR').stdin_content(log_string)\n")

logs = """INFO  Starting server
ERROR Database connection failed
INFO  Retrying connection
ERROR Authentication failed for user 'admin'
INFO  Connection established"""

errors = capture(grep('ERROR').stdin_content(logs))
print(f"  Errors found:\n{errors.read_stdout()}")

# -----------------------------------------------------------------------------
# 3. Conditional chains with Python callbacks
# -----------------------------------------------------------------------------
print("▶ Conditional execution (+ for &&, - for ||):\n")
print("  command + on_success - on_failure\n")

(
    prog('test')('-d', 'shell')
    + (lambda: print("  ✓ 'shell/' directory exists"))
    - (lambda: print("  ✗ 'shell/' directory missing"))
)()

(
    prog('test')('-f', 'nonexistent.txt')
    + (lambda: print("  ✓ File exists"))
    - (lambda: print("  ✗ File not found (expected)"))
)()

# -----------------------------------------------------------------------------
# 4. Python generator as pipeline source
# -----------------------------------------------------------------------------
print("\n▶ Python generator feeding shell pipeline:\n")
print("  generator_func | grep | sort\n")


def server_names():
    """Generator that yields server names."""
    for environment in ['prod', 'staging', 'dev']:
        for i in range(1, 4):
            yield f"{environment}-web-{i:02d}.example.com"


print("  Production servers:")
(server_names | grep('^prod'))()

# -----------------------------------------------------------------------------
# 5. Real-world task: Codebase analysis
# -----------------------------------------------------------------------------
print("\n▶ Real task: Analyze imports in codebase\n")


def analyze_imports():
    """Parse import statements and show statistics."""
    import re

    imports: dict[str, int] = {}
    for line in sys.stdin:
        m = re.match(r'(?:from\s+(\S+)|import\s+(\S+))', line.strip())
        if m:
            mod = (m.group(1) or m.group(2)).split('.')[0]
            imports[mod] = imports.get(mod, 0) + 1

    print("  Top imported modules:")
    for mod, count in sorted(imports.items(), key=lambda x: -x[1])[:6]:
        if mod:
            print(f"    {mod:15} {'█' * count} ({count})")


(
    prog('find')('shell', '-name', '*.py', '-exec', 'cat', '{}', '+')
    | grep('-E', '^(import|from)')
    | analyze_imports
)()

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
print(
    """
╔═════════════════════════════════════════════════════════════════╗
║  Key Features Demonstrated:                                     ║
║                                                                 ║
║  • Python functions in pipelines (just use sys.stdin/print)     ║
║  • Generators as pipeline sources (yield strings)               ║
║  • stdin_content() to pipe Python data to commands              ║
║  • Conditional chains with + (&&) and - (||)                    ║
║  • Lambda callbacks for success/failure handling                ║
║                                                                 ║
║  The | operator seamlessly connects shell commands, Python      ║
║  functions, and generators - no subprocess boilerplate!         ║
╚═════════════════════════════════════════════════════════════════╝
"""
)
