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
