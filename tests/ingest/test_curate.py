import json

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.curate import Curation, MergeRule, ReviewRule, apply_curation, load_curation
from directory.models import Mosque, Occurrence, Source


def _mosque(s, mid, name, url, *, lat=51.0, lng=-1.0):
    s.add(Mosque(id=mid, name=name, lat=lat, lng=lng, website_url=url))


def test_merge_keeps_survivor_drops_other(engine):
    with session_scope(engine) as s:
        _mosque(s, "keep", "Masjid Alhikmah and Community Centre", "https://x.example")
        _mosque(s, "gone", "Masjid Alhikmah", "https://x.example")
        s.add(Occurrence(mosque_id="gone", date="2026-06-21", prayer="fajr",
                         session_idx=0, jamaah_time="05:00"))
        s.add(Source(id="gone", mosque_id="gone", url="https://x.example",
                     triage_status="candidate"))

    summary = apply_curation(engine, Curation(
        merge=[MergeRule(survivor="keep", drop=["gone"], reason="dupe", url="https://x.example")],
        shared_url_review=[],
    ))

    assert summary.merged == 1
    with session_scope(engine) as s:
        assert repo.get_mosque(s, "gone") is None
        keep = repo.get_mosque(s, "keep")
        assert keep is not None
        # the dropped name is preserved as an alias
        assert "Masjid Alhikmah" in keep.aliases_list
        # the occurrence is repointed to the survivor
        occ = list(s.scalars(repo.select(Occurrence).where(Occurrence.mosque_id == "keep")))
        assert len(occ) == 1
        # the dropped source is gone
        assert s.get(Source, "gone") is None


def test_merge_is_idempotent(engine):
    with session_scope(engine) as s:
        _mosque(s, "keep", "A", "https://x.example")
        _mosque(s, "gone", "B", "https://x.example")
    rule = Curation(merge=[MergeRule(survivor="keep", drop=["gone"], reason="dupe",
                                     url="https://x.example")], shared_url_review=[])
    first = apply_curation(engine, rule)
    second = apply_curation(engine, rule)
    assert first.merged == 1
    assert second.merged == 0  # nothing left to merge
    with session_scope(engine) as s:
        assert repo.get_mosque(s, "keep") is not None


def test_merge_handles_occurrence_pk_conflict(engine):
    with session_scope(engine) as s:
        _mosque(s, "keep", "A", "https://x.example")
        _mosque(s, "gone", "B", "https://x.example")
        for mid in ("keep", "gone"):
            s.add(Occurrence(mosque_id=mid, date="2026-06-21", prayer="fajr",
                             session_idx=0, jamaah_time="05:00"))
    apply_curation(engine, Curation(
        merge=[MergeRule(survivor="keep", drop=["gone"], reason="dupe", url="https://x.example")],
        shared_url_review=[],
    ))
    with session_scope(engine) as s:
        occ = list(s.scalars(repo.select(Occurrence).where(Occurrence.mosque_id == "keep")))
        assert len(occ) == 1  # conflicting dropped row discarded, no crash


def test_shared_url_review_flags_for_review(engine):
    with session_scope(engine) as s:
        _mosque(s, "a", "Mosque A", "https://shared.example")
        _mosque(s, "b", "Mosque B", "https://shared.example")
    summary = apply_curation(engine, Curation(merge=[], shared_url_review=[
        ReviewRule(url="https://shared.example", mosque_ids=["a", "b"],
                   reason="2 distinct venues share this URL"),
    ]))
    assert summary.flagged == 2
    with session_scope(engine) as s:
        for mid in ("a", "b"):
            src = repo.source_for_mosque(s, mid)
            assert src.triage_status == "review"
            assert src.review_reason.startswith("shared_url")
            assert src.url == "https://shared.example"


def test_shared_url_flagged_mosques_excluded_from_discovery(engine):
    with session_scope(engine) as s:
        _mosque(s, "a", "Mosque A", "https://shared.example")
        _mosque(s, "b", "Mosque B", "https://shared.example")
        _mosque(s, "solo", "Mosque Solo", "https://solo.example")
    apply_curation(engine, Curation(merge=[], shared_url_review=[
        ReviewRule(url="https://shared.example", mosque_ids=["a", "b"], reason="dupe"),
    ]))
    with session_scope(engine) as s:
        ids = [m.id for m in repo.mosques_for_discovery(s)]
    assert ids == ["solo"]


def test_shared_url_does_not_clobber_authored_source(engine):
    with session_scope(engine) as s:
        _mosque(s, "a", "Mosque A", "https://shared.example")
        s.add(Source(id="a", mosque_id="a", url="https://shared.example/times",
                     triage_status="authored", config='{"shape":"html_table"}'))
    summary = apply_curation(engine, Curation(merge=[], shared_url_review=[
        ReviewRule(url="https://shared.example", mosque_ids=["a"], reason="dupe"),
    ]))
    assert summary.flagged == 0
    assert summary.skipped == 1
    with session_scope(engine) as s:
        assert repo.source_for_mosque(s, "a").triage_status == "authored"


def test_load_curation(tmp_path):
    path = tmp_path / "dupes.json"
    path.write_text(json.dumps({
        "merge": [{"survivor": "keep", "drop": ["gone"], "reason": "dupe",
                   "url": "https://x.example"}],
        "shared_url_review": [{"url": "https://s.example", "mosque_ids": ["a", "b"],
                              "reason": "dupe"}],
    }))
    c = load_curation(path)
    assert c.merge[0].survivor == "keep"
    assert c.merge[0].drop == ["gone"]
    assert c.shared_url_review[0].mosque_ids == ["a", "b"]
