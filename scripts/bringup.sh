#!/usr/bin/env bash
#
# Fresh-state bring-up of the Jamaat Directory extraction pipeline.
#
# Clears the database and runs the FREE, deterministic stages end to end —
# seed → curate → liveness → discovery — then reports the triage breakdown.
# The paid AI authoring stages are printed as the next steps but NOT run here,
# so this is safe to run on a clone without spending model usage.
#
# Usage:
#   scripts/bringup.sh [-y] [RAW_EXPORT.json]
#     -y / --yes      skip the "this clears the DB" confirmation
#     RAW_EXPORT.json upstream MuslimsInBritain export, used to (re)generate the
#                     cleaned seed (data/seed/mosques.json) when it is absent.
#                     The cleaned seed is gitignored, so a fresh clone must pass
#                     this once; later runs reuse the generated seed.
#
set -euo pipefail
cd "$(dirname "$0")/.."

DB="data/directory.db"
SEED="data/seed/mosques.json"
RUN=(uv run directory)

ASSUME_YES=0
RAW_EXPORT=""
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    *)        RAW_EXPORT="$arg" ;;
  esac
done

status() {
  uv run python - <<'PY'
from sqlalchemy import func, select

from directory.config import Settings
from directory.db import make_engine, session_scope
from directory.models import Occurrence, Source

engine = make_engine(Settings().database_url)
with session_scope(engine) as s:
    rows = s.execute(
        select(Source.triage_status, func.count())
        .group_by(Source.triage_status)
        .order_by(func.count().desc())
    ).all()
    for st, n in rows:
        print(f"  {st:16} {n}")
    print(f"  {'TOTAL':16} {sum(n for _, n in rows)}")
    print(f"  occurrences      {s.scalar(select(func.count()).select_from(Occurrence))}")
PY
}

# 0. Confirm, back up, clear -------------------------------------------------
if [[ -f "$DB" && "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "This clears $DB and data/candidates/. Continue? [y/N] " ans
  [[ "$ans" == [yY]* ]] || { echo "aborted"; exit 1; }
fi
if [[ -f "$DB" ]]; then
  bak="$DB.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$DB" "$bak"
  echo ">> backed up existing DB -> $bak"
  rm -f "$DB" "$DB-wal" "$DB-shm"
fi
rm -f data/candidates/*.json 2>/dev/null || true

# 1. Seed + curate (deterministic) ------------------------------------------
if [[ ! -f "$SEED" ]]; then
  if [[ -z "$RAW_EXPORT" ]]; then
    echo "!! $SEED is missing and no raw export was given." >&2
    echo "   Pass the upstream export once:  scripts/bringup.sh <export.json>" >&2
    exit 1
  fi
  "${RUN[@]}" import-mib --input "$RAW_EXPORT"
fi
"${RUN[@]}" init-db
"${RUN[@]}" seed --input "$SEED"
"${RUN[@]}" curate

# 2. Liveness + deterministic discovery (free, £0) --------------------------
"${RUN[@]}" validate-websites
"${RUN[@]}" discover   # platform-detect + verify + extract inline; rest -> candidate

# 3. Report ------------------------------------------------------------------
echo
echo "=== triage breakdown (post-discovery) ==="
status

cat <<'EOF'

Deterministic stages done. The candidate count above is the AI authoring queue.

Next (paid AI authoring — NOT run by this script):
  uv run directory author --concurrency 1 --max-calls 300   # single-shot, synchronous, resumable
  uv run directory reauthor                                 # FREE verify-retry of needs_reauthor
  uv run directory reauthor --no-verify-only --agentic      # agentic browse on the remainder
EOF
