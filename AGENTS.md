# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Dependency Management

Use `uv` for managing dependencies rather than editing `pyproject.toml` directly:

```bash
uv add <package>           # Add runtime dependency
uv add --dev <package>     # Add dev dependency
uv remove <package>        # Remove dependency
uv sync                    # Sync lockfile with pyproject.toml
```

## Testing

Run all tests with:
```bash
uv run pytest tests/ -v
```

Test organization:
- `tests/test_bash_compat.py` - Integration tests comparing against real bash
- `tests/test_bash_pure.py` - Unit tests for pure functions
- `tests/test_harness_smoke.py` - Harness smoke tests

See `tests/AGENTS.md` for detailed testing patterns and the test harness API.

## Component Documentation

Component-specific patterns and debugging utilities:
- `tests/AGENTS.md` - Test harness API and testing patterns
- `shell/compat/AGENTS.md` - Bash interpreter debugging and visitor patterns

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

