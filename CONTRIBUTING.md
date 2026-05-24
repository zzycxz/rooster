# Contributing to Rooster

## Dev Setup

```bash
git clone git@github.com:zzycxz/rooster.git
cd rooster
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Code Style

- **Line length**: 120
- **Lint/format**: `ruff check src/ tests/ && ruff format --check src/ tests/`
- **Comments**: bilingual — `# 中文说明 / English description`
- **No bare `except:`** — always `except Exception as e:`
- **Type hints**: use on public functions

Run before committing:

```bash
ruff check src/ tests/ --fix
ruff format src/ tests/
pytest tests/ -v --tb=short
```

## Adding a Tool

1. Create `src/toolset/definitions/<name>.py`
2. Inherit `BaseTool`, define `args_schema` with Pydantic
3. Implement `async def run(self, **kwargs) -> str`
4. Add bilingual comments on key logic
5. Add tests in `tests/`

## PR Guidelines

- One concern per PR
- CI must pass (lint + test + security audit)
- Commit message: `type(scope): description` (e.g. `fix(auth): handle expired token`)
