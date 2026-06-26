"""Authoring eval harness (Phase 7).

A data-driven regression suite over ``tests/fixtures/authoring_cases/``. Each case
is a small JSON describing a mosque's fetched pages (HTML fixtures), an optional
model reply, and the expected outcome. The harness seeds one mosque, runs the real
deterministic discovery funnel against a fake fetcher/renderer, then — only if a
case supplies a model reply — runs one authoring stage with a fake harness.

The point is to lock in *where* a source is resolved: most cases must reach their
terminal status with **no model call at all** (deterministic discovery/enumeration),
and the few that need a model are explicit and narrow. ``expected.model_used``
asserts that boundary, so a future regression that starts leaning on the model for a
deterministic shape is caught here.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from directory import repository as repo
from directory.db import init_db, make_engine, session_scope
from directory.ingest.author import author_mosque
from directory.ingest.discover import discover_mosque
from directory.ingest.fetch import FetchResult, html_hash
from directory.models import Mosque
from tests.conftest import FakeHarness

CASES_DIR = Path(__file__).parent.parent / "fixtures" / "authoring_cases"
PAGES_DIR = CASES_DIR / "pages"
TODAY = date(2026, 6, 1)
HORIZON = 20


def _load_cases() -> list[dict]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        cases.append(data)
    return cases


def _html_map(entries: list[dict]) -> dict[str, str]:
    return {e["url"]: (PAGES_DIR / e["html_fixture"]).read_text() for e in (entries or [])}


def _make_fetcher(static: dict[str, str], rendered: dict[str, str]):
    """A fetcher serving fixture HTML by URL. A ``requires_js`` fetch is served from
    the rendered map (falling back to static), mirroring a real headless render."""

    def _f(url, *, requires_js=False, renderer=None, client=None, etag=None,
           last_modified=None, timeout=20.0, nav_renderer=None):
        table = rendered if requires_js else static
        html = table.get(url) or (static.get(url) if requires_js else None)
        if html is None:
            return FetchResult(url, 404, None, None, error="404")
        return FetchResult(url, 200, html, html_hash(html))

    return _f


def _live_client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )


def _run_case(case: dict, engine, candidate_root: Path) -> tuple[str, bool]:
    """Run discovery (+ one model stage when the case supplies a reply) for a case.
    Returns ``(discovery_platform, model_used)``."""
    mid = case["mosque_id"]
    with session_scope(engine, write=True) as s:
        s.add(Mosque(id=mid, name=mid, lat=52.0, lng=-1.0, website_url=case["base_url"]))

    static = _html_map(case["pages"])
    rendered = _html_map(case.get("rendered", []))
    fetcher = _make_fetcher(static, rendered)
    # A renderer sentinel (truthy, never actually called — the fake fetcher serves
    # the rendered HTML) so discovery exercises its JS-shell render retry.
    renderer = object() if rendered else None

    disc = discover_mosque(
        engine, mid, fetcher=fetcher, client=_live_client(), candidate_root=candidate_root,
        today=TODAY, horizon_days=HORIZON, renderer=renderer,
    )

    if disc.outcome != "candidate":
        return disc.platform, False  # resolved deterministically — no model touched

    model_output = case.get("model_output")
    if model_output is None:
        # Deterministic-only authoring pass: no model, leaves source a candidate.
        author_mosque(
            engine, mid, harness=FakeHarness(""), candidate_root=candidate_root,
            models=("cheap",), today=TODAY, horizon_days=HORIZON, fetcher=fetcher,
            no_model=True,
        )
        return disc.platform, False

    harness = FakeHarness(model_output)
    author_mosque(
        engine, mid, harness=harness, candidate_root=candidate_root, models=("cheap",),
        today=TODAY, horizon_days=HORIZON, fetcher=fetcher, feedback_retries=0,
    )
    return disc.platform, bool(harness.calls)


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["mosque_id"])
def test_authoring_case(case, tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'eval.db'}")
    init_db(engine)
    platform, model_used = _run_case(case, engine, tmp_path)
    expected = case["expected"]

    with session_scope(engine) as s:
        src = repo.get_source(s, case["mosque_id"])
        assert src is not None, "discovery should always create a source row"
        triage_status, last_status, shape = src.triage_status, src.last_status, src.shape

    assert triage_status == expected["triage_status"]
    assert model_used == expected["model_used"]

    if "last_status" in expected:
        assert last_status == expected["last_status"]
    if "config_shape" in expected:
        assert shape == expected["config_shape"]
    if "platform" in expected:
        assert platform == expected["platform"]


def test_eval_suite_is_mostly_deterministic():
    """Guard rail on the suite itself: the overwhelming majority of cases must
    resolve with no model call, so the harness keeps measuring deterministic
    coverage rather than silently drifting into model-backed resolution."""
    cases = _load_cases()
    model_cases = [c for c in cases if c["expected"]["model_used"]]
    assert len(cases) >= 10
    assert len(model_cases) <= 2  # model-router cases stay explicit and few
