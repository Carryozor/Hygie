# Contributing to Hygie

## Getting started

```bash
git clone https://github.com/Carryozor/Hygie.git && cd Hygie
pip install -r requirements.txt -r requirements-dev.txt
make dev        # starts uvicorn with --reload on :8000
make test       # runs the full test suite
```

Requirements: Python 3.11+, pip.

## Project structure

```
backend/        FastAPI app, scheduler, arr/media clients
  db/           SQLite layer (utils, settings_store, repositories, …)
  arr_clients/  Radarr, Sonarr, Seerr, qBittorrent
  routers/      One file per API route group
frontend/
  static/       CSS, JS (vanilla), images
  templates/    Jinja2 index.html
tests/          pytest — run with `make test`
docs/superpowers/plans/  Implementation plans (historical)
```

## Running tests

```bash
make test                          # full suite (fast, ~3s)
python3 -m pytest tests/test_repositories.py -v   # single module
```

Tests use an in-memory SQLite via `tmp_path` — no external services needed. The suite must pass before a PR is merged.

## Pull request process

1. Fork the repo, create a branch from `main` (`feat/my-feature` or `fix/my-bug`).
2. Write or update tests for any behaviour change.
3. Ensure `make test` passes.
4. Open a PR against `main`. Describe **what** changed and **why**.
5. Keep PRs focused — one feature or fix per PR.

## Code style

- Python 3.11+, type hints on all public functions.
- `async`/`await` throughout — no blocking I/O in the event loop.
- All DB access through `backend/db/repositories.py` — do not write raw SQL in routers.
- No bare `except:` — catch specific exceptions.
- No comments explaining *what* the code does — names should be self-documenting. Comments reserved for non-obvious *why*.

## Commit messages

Use the conventional format:
```
feat: add Plex media server support
fix: guard ArrClientError in run_scan_library
refactor: extract _seerr_pages() async generator
chore: bump version to 2.8.0
```

## What we're looking for

- Bug fixes with a regression test
- Plex / Jellyfin / Emby compatibility improvements
- Performance improvements (especially scan and deletion throughput)
- Better observability (metrics, logging)

Please open an issue before starting large features — it avoids duplicate work.

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) or open a [GitHub issue](https://github.com/Carryozor/Hygie/issues).

For security issues, see [SECURITY.md](SECURITY.md).
