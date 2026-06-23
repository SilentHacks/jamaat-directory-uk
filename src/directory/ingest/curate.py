"""Apply a reviewed duplicate-curation overlay to the seeded database.

The mosque list is rebuilt from the upstream export on every seed, so dedupe
decisions live in a git-tracked overlay file (``data/curation/duplicates.json``)
and are re-applied by ``directory curate`` after ``directory seed`` — never as a
one-off hand edit that a reseed would silently undo.

Two operations, matching the two real shapes in the data:

* ``merge`` — genuinely co-located duplicate records (same venue entered twice).
  The survivor is kept; the rest are folded in and deleted.
* ``shared_url_review`` — several *distinct* venues (umbrella org sites, satellite
  Jumu'ah halls, mis-assigned URLs) that happen to share one exact URL. Both rows
  are kept (they are separately routable), but each is flagged for review so the
  automatic discovery funnel skips it and never misattributes one site's
  timetable to a venue it doesn't describe.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from directory import repository as repo
from directory.db import session_scope

# Authored sources carry verified timetable data; a shared-URL flag must never
# clobber them. Only these provisional states are safe to re-flag as review.
_FLAGGABLE = frozenset({"candidate", "review", "blocklisted"})


@dataclass
class MergeRule:
    survivor: str
    drop: list[str]
    reason: str
    url: str | None = None


@dataclass
class ReviewRule:
    url: str
    mosque_ids: list[str]
    reason: str


@dataclass
class Curation:
    merge: list[MergeRule]
    shared_url_review: list[ReviewRule]


@dataclass
class CurationSummary:
    merged: int = 0
    flagged: int = 0
    skipped: int = 0


def load_curation(path: Path) -> Curation:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Curation(
        merge=[MergeRule(**rule) for rule in data.get("merge", [])],
        shared_url_review=[ReviewRule(**rule) for rule in data.get("shared_url_review", [])],
    )


def apply_curation(engine: Engine, curation: Curation) -> CurationSummary:
    summary = CurationSummary()
    with session_scope(engine, write=True) as s:
        for rule in curation.merge:
            for drop_id in rule.drop:
                if repo.merge_mosque(s, rule.survivor, drop_id):
                    summary.merged += 1

        for rule in curation.shared_url_review:
            reason = f"shared_url: {rule.reason}"
            for mosque_id in rule.mosque_ids:
                if repo.get_mosque(s, mosque_id) is None:
                    summary.skipped += 1
                    continue
                existing = repo.source_for_mosque(s, mosque_id)
                if existing is not None and existing.triage_status not in _FLAGGABLE:
                    summary.skipped += 1
                    continue
                source_id = existing.id if existing is not None else mosque_id
                repo.create_or_update_source(
                    s,
                    source_id=source_id,
                    mosque_id=mosque_id,
                    url=rule.url,
                    platform=None,
                    shape=None,
                    config=None,
                    requires_js=False,
                    triage_status="review",
                )
                repo.set_source_state(s, source_id, review_reason=reason)
                summary.flagged += 1
    return summary
