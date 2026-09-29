# Contributing to AETHER

Thanks for helping. AETHER has one rule that outranks style: **it observes, it
never acts.** Any PR that adds an execution path will be declined.

## Getting set up

```bash
pip install -e ".[win,dev]"
pytest -q
ruff check aether tests
```

## Ground rules

- **No new required dependencies.** The core runs on the standard library.
  Anything else belongs in an extra.
- **Everything stays injectable.** New subsystems take their dependencies as
  constructor arguments so tests can fake them.
- **Redact before you prompt.** If data can reach the LLM, it must pass through
  `aether/redact.py` first.
- **New behaviour needs a test.** The suite must run headless, on any OS.
- **Bump the risk tier if unsure.** An unknown action is `medium`, not `low`.

## Commit style

Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.

```bash
git commit -m "feat(overlay): add click-through regions"
```

## Reporting bugs

Include your OS, Python version, `aether --doctor` output, and the relevant
log lines. Please redact anything sensitive from logs before posting.
