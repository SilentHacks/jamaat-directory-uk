# AGENTS.md

Essential guide for autonomous agents working on the **UK Mosque Jamaat
Directory**. Read this before making changes. See
<https://docs.factory.ai/factory-docs/agents-md> for the spec this follows.

## What this is

A lean backend recording UK mosque **jamaat** (congregational prayer)
timetables, served via a JSON API (`/v1`) and a small Jinja/HTMX browse site.
The data layer feeding **Sirat**, a separate journey-planner. Stack is
deliberately small: one Python process, one SQLite file, one cron job. No
Celery, Redis, PostGIS, or object store. See `DESIGN.md` for the full spec.

## Environment

- **Language:** Python 3.12+.
- **Package manager:** `uv` (lockfile committed at `uv.lock`).
- **One-time setup:** `uv sync --extra dev`
  - Optional, for JS-rendered sources only: `uv sync --extra js` then
    `uv run playwright install chromium`. The served container and CI do **not**
  need the `js` extra.
- **Env vars:** `pydantic-settings`, all prefixed `DIRECTORY_` (see
  `src/directory/config.py`). Required for serving/admin:
  - `DIRECTORY_DB_PATH` (default `data/directory.db`)
  - `DIRECTORY_ADMIN_API_KEY` (gates `/v1/admin/*` + the HTMX review queue)
  - `DIRECTORY_SNAPSHOT_HORIZON_DAYS` (default `45`)
  - Authoring harness knobs: `DIRECTORY_AUTHOR_HARNESS`
    (`claude-code`|`opencode`|`command-code`|`kimchi`|`cursor`, default
    `claude-code`), plus `DIRECTORY_CLAUDE_CODE_MODEL`,
    `DIRECTORY_AUTHOR_MAX_CALLS`, `DIRECTORY_AUTHOR_HARNESS_TIMEOUT`.
  - Production template: `.env.production.example`.

## Common commands

All commands run through `uv run directory <command>` (the `directory` console
script is defined in `pyproject.toml` -> `src/directory/cli.py`).

### Dev loop

```bash
uv sync --extra dev                 # install deps (dev extras: pytest, httpx, ruff)
uv run directory init-db            # create SQLite + schema (src/directory/schema.sql)
uv run directory serve              # API + browse site at http://127.0.0.1:8000
```

Interactive OpenAPI docs at `/docs`; browse site at `/`. Public reads under
`/v1` are open; `/v1/admin/*` needs `X-API-Key` (or `?key=`).

### Lint, format, test

```bash
uv run ruff check                   # lint (rules: E,F,I,UP,B; line-length 100)
uv run ruff format                  # format
uv run pytest -q                    # full suite (~650 tests, <1s collect)
uv run pytest --collect-only -q     # just verify tests are runnable
uv run pytest tests/api -q          # one subtree (api|core|ingest|web)
```

CI (`.github/workflows/ci.yml`) runs exactly: `uv sync --extra dev` ->
`uv run ruff check` -> `uv run pytest -q`. Keep both green before committing.

### Ingest pipeline (deterministic stages are free; AI stages are paid)

```bash
uv run directory import-mib --input mib_uk_ie_mosques.json   # clean upstream export -> data/seed/mosques.json
uv run directory seed --input data/seed/mosques.json         # load mosque list
uv run directory curate                                      # apply duplicate overlay (data/curation/duplicates.json)
uv run directory validate-websites                           # verify-or-empty known websites
uv run directory discover                                    # liveness -> platform -> gather (run after seed+curate)
uv run directory discover --mosque-id <id>                   # one mosque
uv run directory inspect-candidate --mosque-id <id>          # dry-run a candidate (no writes, no model)
```

`scripts/bringup.sh` chains the free stages end to end (seed -> curate ->
liveness -> discovery) and reports the triage breakdown. It stops before the
paid AI stages. Safe to run on a fresh clone; pass the raw export once to
regenerate the gitignored seed.

### AI authoring (paid) + daily extract

```bash
uv run directory author                       # single-shot authoring backlog (Claude Code, Opus 4.8 @low)
uv run directory author --concurrency 1       # serialize; resumable (completed sources skipped)
uv run directory author --no-model            # deterministic-only recovery pass (no spend)
uv run directory reauthor                     # FREE: re-extract retained configs, no model
uv run directory reauthor --no-verify-only    # model re-author of needs_reauthor remainder
uv run directory extract                      # daily deterministic extract over authored sources (60-day horizon)
uv run directory extract --source-id <id>     # one source
```

Authoring harnesses (`claude-code` default; also `opencode`, `command-code`,
`kimchi`, `cursor`) are configured in `src/directory/ingest/harness.py`. Every
authored config must pass the QC gates (`src/directory/ingest/gates.py`) before
it activates — the harness only fills configs the engine already runs.

## Project layout

```
src/directory/
  config.py          pydantic-settings (DIRECTORY_ env prefix)
  domain.py          Prayer enum + core dataclasses
  db.py              engine/session helpers; init_db() runs schema.sql
  models.py          SQLAlchemy 2.0 typed models (Mosque, Source, Occurrence, ExtractorRun)
  repository.py      data access (swappable behind this; SQLite engine today)
  schemas.py         Pydantic API response models
  cli.py             Typer CLI — the `directory` entry point
  api/               FastAPI app.py + mosques.py, times.py, admin.py, deps.py
  web/               Jinja browse site + HTMX review queue (routes.py, templates/, static/)
  ingest/
    seed.py          MIB export cleaning + seeding
    website.py       verify-or-empty website validation
    discover.py      discovery funnel (liveness -> platform -> gather)
    fetch.py         httpx + optional Playwright render
    author.py        single-shot config authoring + agentic fallback
    harness.py       AI agent harness backends
    normalize.py     shared time/date/prayer-name resolvers
    gates.py         QC signals + lane routing (auto-accept/review/reject)
    runner.py        daily deterministic extract
    extractors/      shape dispatch (html_table, html_repeated, dom_grid, ...)
tests/               api/ core/ ingest/ web/ + fixtures/; conftest.py builds a tmp SQLite per test
```

## Conventions

- **Lean stack is a design choice, not a TODO.** Do not introduce Celery, Redis,
  PostGIS, object stores, or any infra that earns its place only at a scale this
  dataset (~2,100 mosques) does not have.
- **Types:** SQLAlchemy 2.0 `Mapped[...]` style (see `models.py`); Pydantic v2
  for API schemas (`schemas.py`). Keep type hints accurate. (No mypy is
  configured yet — match the existing typing style.)
- **Style:** Ruff rules `E,F,I,UP,B`, line length 100. Run `ruff check` and
  `ruff format` before committing; CI will fail on lint.
- **Data flow is one-way:** `MIB -> seed -> curate -> validate-websites ->
  discover -> author -> extract -> occurrences`. Occurrences are the product;
  downstream (Sirat) never reinterprets monthly tables.
- **`website_url = NULL` is a first-class, acceptable state.** Discovery
  flakiness must never write a wrong site. Verify-or-leave-empty.
- **Wrong data is fixed by re-running the extractor**, not by hand-editing
  rows. The review queue is `triage_status='review'` (a view, not a table).
- **Daily extract is deterministic and zero-AI.** AI lives only in the one-time
  `author`/`reauthor` stages; its output is a cached `config` on the `source`
  row. Never add AI to the daily path.
- **Every config passes `gates.py` before activating.** Bias to queueing
  (`review`) over auto-accept when begin-vs-jamaat is undecidable.
- **Tests** use the `engine`/`seeded` fixtures in `tests/conftest.py` (a fresh
  tmp SQLite per test). Mirror existing `test_*.py` naming and the fixture
  pattern when adding tests. Pytest config (`pythonpath=["src"]`,
  `testpaths=["tests"]`) is in `pyproject.toml`.
- **Secrets:** never hardcode. Load via `pydantic-settings`
  (`DIRECTORY_ADMIN_API_KEY`, etc.). `.env` is gitignored; keep
  `.env.production.example` in sync with new required vars.

## API surface

All read endpoints under `/v1` (full OpenAPI at `/docs`):

| Endpoint | Purpose |
|---|---|
| `GET /v1/mosques` | List; filters `city`, `bbox`, `near`, `has_times`. |
| `GET /v1/mosques/{id}` | Mosque detail + source status. |
| `GET /v1/mosques/{id}/times` | Jamaat times for a date or `from`/`to` range. |
| `GET /v1/times` | All jamaat times matching spatial + date + prayer filter. |
| `GET /v1/snapshot` | Whole mosques + upcoming-times set (ETag/conditional-GET). |
| `GET /v1/health` | Liveness: `{status, mosques}`. |
| `GET /v1/admin/sources`, `/admin/review` | Ops (admin API key). |
| `POST /v1/admin/sources/{id}/refresh`, `/admin/mosques/{id}/discover`, `/admin/mosques/{id}/author` | Ops actions. |

`jumuah` is returned as an array of `{label, time}` sessions; daily prayers are
scalars.
