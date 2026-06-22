# UK Mosque Jamaat Directory

[![CI](https://github.com/SilentHacks/jamaat-directory-uk/actions/workflows/ci.yml/badge.svg)](https://github.com/SilentHacks/jamaat-directory-uk/actions/workflows/ci.yml)

A lean backend that records UK mosque **jamaat** (congregational prayer)
timetables and serves them via a JSON API and a small browse site. It is the
data layer feeding **Sirat**, a journey-planning app that routes users past
mosques in time for jamaat.

> **Status:** early development. The API ships on seed data (mosque list +
> coordinates) before timetable extraction exists, so consumers can integrate
> immediately against empty `times`.

## Why it's small on purpose

The dataset is tiny (~2,100 mosques). One process, one SQLite file, one cron
job — every component earns its place. No Celery, Redis, PostGIS, or object
store.

## Quick start

```bash
uv sync --extra dev
uv run directory import-mib --input mib_uk_ie_mosques.json   # clean upstream export
uv run directory seed --input data/seed/mosques.json         # load mosque list
uv run directory serve                                       # http://127.0.0.1:8000
```

### Daily extract (deterministic, zero AI)

Once sources are authored (Phase 3), refresh the rolling timetable horizon:

```bash
directory extract                 # all authored sources, 60-day horizon
directory extract --source-id s1  # one source
```

The daily run fetches each source's known URL, applies its cached `config`,
runs the quality gates, and upserts `occurrence` rows. A source that breaks
(0 rows or failed gates) is flagged `needs_reauthor` and keeps its last-known
data rather than being wiped.

JS-rendered sources are re-rendered with a headless browser on the daily run
(`--no-render-js` skips it for offline/CI). Timetables split across monthly
pages add a `paging` block to their config — either a `url_template`
(`/{year}/{month:02d}`) fetched once per month in the horizon, or a `render_nav`
spec that drives the headless browser through a JS calendar (clicking a
next-month control or picking from a month/year dropdown). The current month is
required; a future month that isn't published yet is tolerated and flagged
`partial_horizon` until it appears.

### Discovery (one-time, deterministic, £0)

Resolve where each mosque's timetable lives and author a config for the known
platforms — no AI, no live navigation on the daily path:

```bash
directory validate-websites        # verify-or-empty the known websites
directory discover                 # liveness → platform detect → gather
directory discover --mosque-id wp  # one mosque
```

Platform matches (WordPress prayer tables, Mawaqit, MyLocalMasjid, Masjidbox)
are authored and immediately verified through the extraction gates. Everything
else has its timetable candidates gathered and cached under `data/candidates/`
for the Phase-4 AI authoring step.

### Authoring (single-shot, agent harness)

For mosques the deterministic funnel left as `candidate`, hand the cached
candidate bundle to an agent harness to author a `SourceConfig`. The harness is
pluggable (default: the OpenCode CLI; others register via `register_harness`).
Every authored config is verified through the same extraction gates before it
activates — the harness only fills configs the engine already runs.

```bash
directory author                    # author the candidate backlog (cheap→strong)
directory author --mosque-id m1     # one mosque
directory author --max-calls 20     # cap harness calls this run (resumable)
```

Configure via env: `DIRECTORY_AUTHOR_HARNESS`, `DIRECTORY_AUTHOR_MODEL_CHEAP`,
`DIRECTORY_AUTHOR_MODEL_STRONG`, `DIRECTORY_AUTHOR_MAX_CALLS`.

### Review queue

Ambiguous configs land in `triage_status='review'`. The HTMX admin queue
(`GET /admin/review?key=<ADMIN_API_KEY>`) shows each item's source link,
extracted preview, config mapping, and flag reason, with approve / reject /
fix-mapping actions. The key is accepted via the `X-API-Key` header or a `?key=`
query param; run it behind Caddy/Cloudflare in production.

### Agentic fallback (stage 4) + bespoke shape

When single-shot authoring (cheap→strong) cannot map a `candidate`, enable the
stage-4 agentic browsing fallback — a browsing `AuthorHarness` (default: the
OpenCode `browse` agent) that navigates the live site under a per-site
page/token budget (a best-effort directive to the agent; only the subprocess
timeout is a hard ceiling) and emits the **same** `SourceConfig`, or, for a genuinely
unique layout, a `bespoke` Python extractor module.

```bash
directory author --agentic            # author the backlog with the stage-4 fallback
directory author --mosque-id m1 --agentic
```

Bespoke modules the agent writes are persisted under `DIRECTORY_BESPOKE_DIR`
(default `data/bespoke/`) and loaded by `directory extract` before the daily run,
so the deterministic cron path can extract them with **zero** AI. A bespoke module
that raises at runtime yields no rows (the gates then flag the source), never
crashing the run. A fallback that exhausts its budget marks the source
`needs_reauthor` — it never activates an unverified config.

Configure via env: `DIRECTORY_AUTHOR_FALLBACK_HARNESS` (default `agentic`),
`DIRECTORY_AUTHOR_PAGE_BUDGET`, `DIRECTORY_AUTHOR_TOKEN_BUDGET`,
`DIRECTORY_BESPOKE_DIR`.

Interactive API docs at `/docs`. Browse site at `/`.

## API

All endpoints are under `/v1`. See `/docs` for the full OpenAPI schema.

| Endpoint | Purpose |
|---|---|
| `GET /v1/mosques` | List mosques; filter by `city`, `bbox`, `near`, `has_times`. |
| `GET /v1/mosques/{id}` | Mosque detail incl. location + source status. |
| `GET /v1/mosques/{id}/times` | Jamaat times for a date or `from`/`to` range. |
| `GET /v1/times` | All jamaat times matching a spatial + date + prayer filter. |
| `GET /v1/snapshot` | The whole mosques + upcoming-times set as one cached payload. |

## Data sources

Mosque identity and coordinates come from
[MuslimsInBritain](https://www.muslimsinbritain.org/). Timetables are extracted
from each mosque's own website (later milestones).

## License

MIT — see [LICENSE](LICENSE).
