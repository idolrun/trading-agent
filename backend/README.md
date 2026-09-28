# Trading Agent — Backend

FastAPI service for the Discord signal copy-trading assistant. Roadmap and feature specs:
[`docs/implementation/README.md`](../docs/implementation/README.md). Engineering rules:
[`coding-practices.md`](coding-practices.md).

## Commands

```bash
uv sync                                   # install locked deps
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run fastapi dev app/main.py            # dev server: /health, /docs
```

Settings come from environment variables or the repo-root `.env` (see `app/config.py`).
