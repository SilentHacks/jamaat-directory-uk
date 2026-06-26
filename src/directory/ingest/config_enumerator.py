"""Deterministic config enumeration.

Before any model call, code tries the obvious configs a page's structured
evidence implies — table orientations, media (image/PDF) links, recognised
widgets, optional month paging — and verifies each in memory. The obvious cases
(a clean monthly table, a PDF timetable, a Mawaqit widget) are authored for £0;
the model only ever sees what this cannot resolve.

Nothing here is ever persisted directly: every candidate is run through
``verify_candidate`` and only the best *verified* one is handed back, so a
low-quality guess cannot reach the DB.
"""

from collections.abc import Callable
from datetime import date

from directory.ingest.authoring_candidates import ConfigCandidate
from directory.ingest.evidence import (
    MEDIA_TIMETABLE_SCORE,
    NavHint,
    PageEvidence,
)
from directory.ingest.extractors.config_schema import (
    MediaSpec,
    NavSpec,
    PagingSpec,
    SourceConfig,
    WidgetSpec,
)
from directory.ingest.extractors.engine import WIDGET_EXTRACTORS
from directory.ingest.extractors.platforms import base as platforms
from directory.ingest.extractors.platforms.generic_table import (
    _horizontal_multiday,
    _horizontal_single_day,
    _transpose_multiday,
    _vertical_single_day,
)
from directory.ingest.fetch import FetchResult, fetch, html_hash
from directory.ingest.verify import VerifyAttempt, verify_candidate

# Catch-all detectors: a real platform match should outrank these when ranking.
_GENERIC_PLATFORMS = frozenset({"generic_table", "dom_grid", "dom_records"})

# Caps so a noisy page cannot spawn a huge verify fan-out (each verify is a fetch).
_MAX_TABLES = 6
_MAX_MEDIA = 4


# ── table candidates ────────────────────────────────────────────────────────


def _table_confidence(prayers: int, time_count: int) -> float:
    """A table naming more prayers and carrying more clock times is a stronger
    timetable bet. Scaled into roughly 0.5–1.0."""
    base = 0.5 + 0.1 * min(prayers, 5)
    return min(1.0, base + (0.0 if time_count else -0.2))


def _table_candidates(page: PageEvidence) -> list[ConfigCandidate]:
    out: list[ConfigCandidate] = []
    for t in page.tables[:_MAX_TABLES]:
        matrix = t.matrix
        if not matrix:
            continue
        header = t.header
        body = matrix[t.header_depth:]
        conf = _table_confidence(len(t.prayers_named), t.time_count)
        # Orientation helpers, richest first; the engine reads each the same way.
        orientations = (
            ("table_horizontal_multiday", _horizontal_multiday(t.selector, header, body)),
            ("table_transpose_multiday", _transpose_multiday(t.selector, matrix)),
            ("table_horizontal_single_day", _horizontal_single_day(t.selector, header, body)),
            ("table_vertical_single_day", _vertical_single_day(t.selector, header, body)),
        )
        # Richest recognised orientation wins per table (helpers are ordered
        # richest-first), mirroring the generic_table detector.
        for name, config in orientations:
            if config is None:
                continue
            out.append(
                ConfigCandidate(
                    url=page.url, config=config, source=f"enumerator:{name}",
                    reason=f"{name} from {t.table_id}", confidence=conf,
                )
            )
            out.extend(_paged_variant(page, config, name))
            break
    return out


def _is_multiday(config: SourceConfig) -> bool:
    """A multi-day, date-bearing table layout — the only shape paging may attach to
    (gates._lint_paging rejects paging on single_day / prayer-rows / rules)."""
    grid = config.grid
    return bool(
        grid is not None
        and not grid.single_day
        and grid.prayer_label_index is None
        and grid.date is not None
    )


def _paging_from_nav(nav: NavHint) -> PagingSpec | None:
    if nav.kind == "next" and nav.next_selector:
        return PagingSpec(
            mode="render_nav",
            nav=NavSpec(kind="next", next_selector=nav.next_selector,
                        ready_selector=nav.ready_selector),
        )
    if nav.kind == "select" and nav.month_select:
        return PagingSpec(
            mode="render_nav",
            nav=NavSpec(kind="select", month_select=nav.month_select,
                        year_select=nav.year_select, ready_selector=nav.ready_selector),
        )
    return None


def _paged_variant(
    page: PageEvidence, config: SourceConfig, name: str
) -> list[ConfigCandidate]:
    """A page showing one month with a month control yields a paged variant of a
    multi-day table, so the daily run walks the whole horizon. Emitted alongside
    the unpaged config: if no nav renderer is available the paged one simply fails
    to verify and the unpaged candidate stands."""
    if not page.nav_hints or not _is_multiday(config):
        return []
    paging = _paging_from_nav(page.nav_hints[0])
    if paging is None:
        return []
    paged = config.model_copy(deep=True)
    paged.paging = paging
    return [
        ConfigCandidate(
            url=page.url, config=paged, source=f"enumerator:{name}_paged",
            reason=f"{name} with month paging", confidence=0.6, requires_js=True,
        )
    ]


# ── media candidates ──────────────────────────────────────────────────────────


def _media_candidates(page: PageEvidence) -> list[ConfigCandidate]:
    out: list[ConfigCandidate] = []
    for m in page.media_links:
        # Only a confidently timetable-looking link (prayer/timetable/month/year in
        # url or text) — an opaquely named PDF is left for the model to judge.
        if m.score < MEDIA_TIMETABLE_SCORE:
            continue
        config = SourceConfig(shape=m.kind, media=MediaSpec(url=m.url))
        out.append(
            ConfigCandidate(
                url=page.url, config=config, source=f"enumerator:media_{m.kind}",
                reason=f"{m.kind} timetable link {m.url}",
                confidence=min(1.0, m.score / 4.0),
            )
        )
        if len(out) >= _MAX_MEDIA:
            break
    return out


# ── widget candidates ─────────────────────────────────────────────────────────


def _widget_candidates(page: PageEvidence) -> list[ConfigCandidate]:
    """Recognised embedded prayer widgets — only providers that have a registered
    widget extractor (a bare/unknown widget is never emitted; the gates would
    reject it anyway and the engine could not read it)."""
    out: list[ConfigCandidate] = []
    for w in page.widget_hints:
        if w.provider not in WIDGET_EXTRACTORS:
            continue
        config = SourceConfig(
            shape="widget", widget=WidgetSpec(platform=w.provider, data_url=w.data_url)
        )
        out.append(
            ConfigCandidate(
                url=w.data_url or page.url, config=config,
                source=f"enumerator:widget_{w.provider}",
                reason=f"{w.provider} prayer widget", confidence=w.confidence,
            )
        )
    return out


def enumerate_candidates(evidence: list[PageEvidence]) -> list[ConfigCandidate]:
    """All deterministic config candidates a set of page evidences implies."""
    out: list[ConfigCandidate] = []
    for page in evidence:
        out.extend(_table_candidates(page))
        out.extend(_media_candidates(page))
        out.extend(_widget_candidates(page))
    return out


def detect_candidates(
    html_by_url: dict[str, str], *, fetcher=fetch
) -> list[ConfigCandidate]:
    """Run the registered platform detectors over already-fetched HTML and wrap any
    matches as candidates, so detector and enumerator results rank and verify
    through one uniform path (used by the author recovery pass, where no detection
    has run yet)."""
    out: list[ConfigCandidate] = []
    for url, html in html_by_url.items():
        match = platforms.detect_platform(html, url, fetcher=fetcher)
        if match is None:
            continue
        out.append(
            ConfigCandidate(
                url=match.url, config=match.config, source=f"platform:{match.platform}",
                reason=f"platform detector {match.platform}", confidence=0.9,
                requires_js=match.requires_js,
            )
        )
    return out


# ── verification + ranking ──────────────────────────────────────────────────


_LANE_RANK = {"auto_accept": 2, "review": 1, "deferred": 0}


def _specific_rank(cand: ConfigCandidate) -> int:
    """Platform-specific match (2) ≻ a generic platform / enumerator guess (1)."""
    platform = cand.platform
    if platform is not None and platform not in _GENERIC_PLATFORMS:
        return 2
    return 1


def _rank_key(attempt: VerifyAttempt, order: int) -> tuple:
    """Best-first ranking: auto_accept ≻ review; platform-specific ≻ generic; more
    rows ≻ fewer; structured HTML ≻ media; higher confidence; earlier ≻ later."""
    cand = attempt.candidate
    structured = 0 if attempt.evaluation.media else 1
    return (
        _LANE_RANK.get(attempt.lane, 0),
        _specific_rank(cand),
        attempt.rows_count,
        structured,
        cand.confidence,
        -order,
    )


def best_verified_candidate(
    candidates: list[ConfigCandidate],
    *,
    today: date | None = None,
    horizon_days: int = 60,
    fetcher=fetch,
    renderer=None,
    nav_renderer=None,
) -> VerifyAttempt | None:
    """Verify every candidate in memory and return the best that passes, or None
    when none verify. Order of ``candidates`` is the final tiebreak (earlier wins)."""
    best: VerifyAttempt | None = None
    best_key: tuple | None = None
    for order, cand in enumerate(candidates):
        attempt = verify_candidate(
            cand, today=today, horizon_days=horizon_days, fetcher=fetcher,
            renderer=renderer, nav_renderer=nav_renderer,
        )
        if not attempt.ok:
            continue
        key = _rank_key(attempt, order)
        if best is None or key > best_key:
            best, best_key = attempt, key
    return best


def cached_fetcher(pages: dict[str, str], real_fetcher=fetch) -> Callable[..., FetchResult]:
    """A fetcher that serves already-fetched HTML from ``pages`` (so a static verify
    costs no extra request), delegating to ``real_fetcher`` for anything not cached
    or for a JS render — used by discovery, which has already fetched the corpus."""

    def _f(url, *, requires_js=False, renderer=None, **kwargs):
        if not requires_js and url in pages:
            return FetchResult(url, 200, pages[url], html_hash(pages[url]))
        return real_fetcher(url, requires_js=requires_js, renderer=renderer, **kwargs)

    return _f
