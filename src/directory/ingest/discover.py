import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.blocklist import is_blocklisted
from directory.ingest.config_enumerator import (
    best_verified_candidate,
    cached_fetcher,
    enumerate_candidates,
)
from directory.ingest.evidence import (
    _TIME_SCAN_RE,
    PageEvidence,
    _distinct_prayers,
    _page_needs_render,
    build_page_evidence,
    terminal_no_timetable,
)
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms import base as platforms
from directory.ingest.fetch import USER_AGENT, FetchResult, fetch
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize
from directory.ingest.pager import collect_documents, extract_documents
from directory.ingest.runner import extract_source
from directory.ingest.verify import persist_verified_candidate

# Re-exported for callers/tests that import these from discover (their historical
# home); the implementation now lives in evidence.py so it is shared without a
# circular import.
__all__ = [
    "_distinct_prayers",
    "_page_needs_render",
    "build_page_evidence",
    "check_liveness",
    "discover_mosque",
    "gather_candidates",
    "run_discovery",
    "strip_to_region",
]


@dataclass
class LivenessResult:
    url: str
    final_url: str | None
    status: int
    alive: bool
    error: str | None = None


def check_liveness(
    url: str, *, client: httpx.Client | None = None, timeout: float = 10.0
) -> LivenessResult:
    owns = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url, headers={"User-Agent": USER_AGENT})
        alive = 200 <= resp.status_code < 400
        return LivenessResult(url, str(resp.url), resp.status_code, alive)
    except httpx.HTTPError as exc:
        return LivenessResult(url, None, 0, False, error=f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            client.close()


RANKED_PATHS: tuple[str, ...] = (
    "/prayer-times",
    "/prayer-time",
    "/prayer_times",
    "/prayer_time",
    "/prayertimes",
    "/prayers",
    "/timetable",
    "/timetables",
    "/time-table",
    "/time-tables",
    "/time_table",
    "/time_tables",
    "/salah",
    "/namaz",
    "/prayer",
    "/times",
)

_KEYWORDS = ("prayer", "timetable", "salah", "salat", "namaz", "jamaah", "iqamah", "times")


@dataclass
class Candidate:
    url: str
    score: float
    region_html: str
    text: str
    # True when ``region_html`` came from a headless render (the static fetch was a
    # JS shell). Carried so the model's verify and the daily extract render this page
    # instead of fetching the empty pre-hydration HTML. Defaulted for backward compat
    # with bundles written before this field existed.
    requires_js: bool = False


@dataclass
class CandidateBundle:
    mosque_id: str
    base_url: str
    candidates: list["Candidate"]
    # Structured per-page evidence (tables, media, widgets, JS/terminal hints) so
    # downstream prompts/enumerators reason over a compact summary instead of raw
    # HTML. Optional and defaulted so a bundle written before this field still
    # loads (backward compatibility).
    evidence: list[PageEvidence] = field(default_factory=list)

    def save(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.mosque_id}.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, mosque_id: str, root: Path) -> "CandidateBundle | None":
        path = root / f"{mosque_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            mosque_id=data["mosque_id"],
            base_url=data["base_url"],
            candidates=[Candidate(**c) for c in data["candidates"]],
            evidence=[PageEvidence.from_dict(e) for e in data.get("evidence", [])],
        )


def _keyword_links(
    html: str, base_url: str, *, blocklist: frozenset[str] | None = None
) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).netloc
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True).lower()
        blob = f"{href.lower()} {text}"
        if not any(k in blob for k in _KEYWORDS):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).netloc != host:
            continue
        if is_blocklisted(absolute, blocklist=blocklist):
            continue
        if absolute not in out:
            out.append(absolute)
    return out


def _table_richness(table) -> tuple[int, int]:
    """Rank a ``<table>`` as a prayer timetable: (distinct daily prayers named,
    clock-time count). A full month's grid carries far more times than a single
    day's widget, so the time count separates the rich source from a thin one
    once both name the same prayers."""
    text = table.get_text(" ", strip=True)
    return _distinct_prayers(text), len(_TIME_SCAN_RE.findall(text))


def strip_to_region(html: str) -> tuple[str, str]:
    """The slice of a page handed to the AI: the richest prayer ``<table>`` on it,
    not merely the first. A page often leads with a thin daily widget while a full
    multi-day timetable sits lower (or a clean per-prayer table alongside it); the
    first-table heuristic fed the agent the weakest source. Pick the table naming
    the most daily prayers and carrying the most clock times; fall back to the
    first table, then the body, when nothing looks like a prayer timetable."""
    soup = BeautifulSoup(html, "lxml")
    best = None
    best_rank = (0, 0)
    for table in soup.find_all("table"):
        prayers, times = _table_richness(table)
        if prayers < 2 or times < 1:  # not a prayer timetable; ignore
            continue
        rank = (prayers, times)
        if best is None or rank > best_rank:
            best, best_rank = table, rank
    if best is not None:
        return str(best), best.get_text(" ", strip=True)
    table = soup.find("table")
    if table is not None:
        return str(table), table.get_text(" ", strip=True)
    body = soup.body or soup
    return str(body), body.get_text(" ", strip=True)


def _score(text: str) -> float:
    low = text.lower()
    hits = sum(low.count(k) for k in _KEYWORDS)
    has_time = ":" in text
    return hits + (5.0 if has_time else 0.0)


def gather_candidates(
    mosque_id: str,
    base_url: str,
    *,
    homepage_html: str | None = None,
    client: httpx.Client | None = None,
    fetcher=fetch,
    max_candidates: int = 5,
    blocklist: frozenset[str] | None = None,
) -> CandidateBundle:
    targets: list[str] = [urljoin(base_url, p) for p in RANKED_PATHS]
    if homepage_html:
        for link in _keyword_links(homepage_html, base_url, blocklist=blocklist):
            if link not in targets:
                targets.append(link)

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for url in targets:
        if url in seen:
            continue
        seen.add(url)
        res = fetcher(url, client=client)
        if not _usable(res):
            continue
        region, text = strip_to_region(res.html)
        candidates.append(Candidate(url=url, score=_score(text), region_html=region, text=text))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return CandidateBundle(mosque_id, base_url, candidates[:max_candidates])


@dataclass
class DiscoverOutcome:
    mosque_id: str
    outcome: str
    platform: str | None
    detail: str | None = None


def _page_set(home_url: str, homepage_html: str, blocklist: frozenset[str] | None) -> list[str]:
    """Ordered, deduped, blocklist-filtered candidate pages: homepage first, then
    the ranked sub-paths, then same-host keyword links from the homepage."""
    ordered: list[str] = [home_url]
    ordered.extend(urljoin(home_url, p) for p in RANKED_PATHS)
    if homepage_html:
        ordered.extend(_keyword_links(homepage_html, home_url, blocklist=blocklist))
    seen: set[str] = set()
    out: list[str] = []
    for url in ordered:
        if url in seen or is_blocklisted(url, blocklist=blocklist):
            continue
        seen.add(url)
        out.append(url)
    return out


@dataclass
class _Verified:
    match: "platforms.PlatformMatch"
    rank: tuple


_LANE_RANK = {"auto_accept": 2, "review": 1}
# Layout-agnostic catch-alls: a real platform / <table> match should outrank
# these, so they all count as non-specific when comparing pages.
_GENERIC_PLATFORMS = frozenset({"generic_table", "dom_grid", "dom_records"})

# Verification horizon for a paging (url_template) config: enough to span the
# current and next month, so paging is exercised and the per-day variation across
# a month boundary clears the constant-column gate, without fetching the whole
# horizon at discovery time. The stored config keeps the full horizon for the
# daily run.
_VERIFY_HORIZON_DAYS = 35


def _verify(html: str, match, *, today: date | None, horizon_days: int, fetcher):
    """Run the engine + gates on a detected page without persisting; returns the
    GateResult and a completeness score, or None when the page does not verify.

    A ``url_template`` paging config's timetable does not live on the handed page
    (it is fetched per month from a data endpoint), so it is verified by walking
    the current + next month through the same pager the daily run uses; the
    self-match gate then runs against those fetched documents, not the handed page."""
    today = today or date.today()
    config = match.config
    paging = config.paging
    if paging is not None and paging.mode == "url_template":
        docs, err = collect_documents(
            config,
            match.url,
            today=today,
            horizon_days=_VERIFY_HORIZON_DAYS,
            requires_js=match.requires_js,
            fetcher=fetcher,
        )
        if err or not docs:
            return None
        result = extract_documents(docs, config, today=today)
        html_text = "\n".join(d.html for d in docs)
    else:
        result = extract(html, config, year=today.year, month=today.month, today=today)
        html_text = html
    rows = materialize(
        result, config, horizon_start=today, horizon_end=today + timedelta(days=horizon_days)
    )
    gate = run_gates(config, result, rows, html_text=html_text)
    if gate.lane not in _LANE_RANK:
        return None
    return gate, len(rows)


def _best_verified(
    pages: list[str],
    fetched_pages: dict[str, str],
    *,
    today: date | None,
    horizon_days: int,
    fetcher,
) -> _Verified | None:
    """Pick the best verified page: auto_accept ≻ review, platform-specific ≻
    generic, more-complete ≻ less, earlier page ≻ later."""
    best: _Verified | None = None
    for idx, url in enumerate(pages):
        html = fetched_pages.get(url)
        if not html:
            continue
        match = platforms.detect_platform(html, url, fetcher=fetcher)
        if match is None:
            continue
        verified = _verify(html, match, today=today, horizon_days=horizon_days, fetcher=fetcher)
        if verified is None:
            continue
        gate, completeness = verified
        is_specific = 0 if match.platform in _GENERIC_PLATFORMS else 1
        rank = (_LANE_RANK[gate.lane], is_specific, completeness, -idx)
        if best is None or rank > best.rank:
            best = _Verified(match=match, rank=rank)
    return best


def _bundle_from_pages(
    mosque_id: str,
    base_url: str,
    fetched_pages: dict[str, str],
    *,
    max_candidates: int = 5,
    evidence: list[PageEvidence] | None = None,
    rendered_urls: frozenset[str] = frozenset(),
) -> CandidateBundle:
    """Build a CandidateBundle from pages already fetched during discovery, so the
    AI hand-off costs no additional requests. The structured per-page evidence
    (already built for the terminal-classification check) rides along so prompts
    and the enumerator do not have to re-parse the raw HTML. ``rendered_urls`` marks
    the pages whose HTML is a headless render, so their candidates flag requires_js."""
    candidates: list[Candidate] = []
    for url, html in fetched_pages.items():
        region, text = strip_to_region(html)
        candidates.append(
            Candidate(
                url=url,
                score=_score(text),
                region_html=region,
                text=text,
                requires_js=url in rendered_urls,
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return CandidateBundle(
        mosque_id, base_url, candidates[:max_candidates], evidence=evidence or []
    )


def _usable(res) -> bool:
    """A fetch result worth handing to the detector: a real body with a
    non-error HTTP status. 4xx/5xx pages (even with a body) are dropped so a
    soft-404 carrying sitewide chrome can never be authored from."""
    return not res.error and bool(res.html) and res.status < 400


def discover_mosque(
    engine,
    mosque_id: str,
    *,
    fetcher=fetch,
    client: httpx.Client | None = None,
    candidate_root: Path,
    today: date | None = None,
    horizon_days: int = 60,
    blocklist: frozenset[str] | None = None,
    renderer: Callable[[str], str] | None = None,
    nav_renderer=None,
    force: bool = False,
) -> DiscoverOutcome:
    with session_scope(engine) as s:
        mosque = repo.get_mosque(s, mosque_id)
        existing = repo.get_source(s, mosque_id)
        website_url = mosque.website_url if mosque else None
        existing_url = existing.url if existing else None
        has_config = existing is not None and existing.config is not None
        existing_platform = existing.platform if existing else None

    # Anti-clobber guard: never re-discover a source that already holds a config.
    # A re-run resets the source to `candidate` and nulls its config (below),
    # destroying a flaky-but-correct config that a free verify-retry could have
    # salvaged. `force=True` opts into the overwrite. Short-circuits before any
    # network so a preserve costs nothing.
    if has_config and not force:
        return DiscoverOutcome(
            mosque_id, "skipped", existing_platform, detail="existing config preserved"
        )

    # Prefer the URL that already produced a verified config; it is a far better
    # discovery seed than the mosque's homepage (which may be generic chrome that
    # never links to the timetable sub-page). Fall back to the homepage if the
    # preferred URL is dead or returns no usable content.
    seed_urls = []
    if existing_url:
        seed_urls.append(existing_url)
    if website_url:
        if not seed_urls or website_url != seed_urls[0]:
            seed_urls.append(website_url)
    if not seed_urls:
        return DiscoverOutcome(mosque_id, "no_website", None)

    live: LivenessResult | None = None
    home_url: str | None = None
    fetched: FetchResult | None = None
    for url in seed_urls:
        live = check_liveness(url, client=client)
        if not live.alive:
            continue
        candidate_home = live.final_url or url
        # Dead-end the resolved host if it is a social/aggregator/maps domain: no
        # fetch, no AI. Checks the *resolved* URL so redirects to a dead host count.
        if is_blocklisted(candidate_home, blocklist=blocklist):
            with session_scope(engine, write=True) as s:
                repo.create_or_update_source(
                    s,
                    source_id=mosque_id,
                    mosque_id=mosque_id,
                    url=candidate_home,
                    platform=None,
                    shape=None,
                    config=None,
                    requires_js=False,
                    triage_status="blocklisted",
                )
            return DiscoverOutcome(mosque_id, "blocklisted", None)
        fetched = fetcher(candidate_home, client=client)
        if _usable(fetched):
            home_url = candidate_home
            break

    if home_url is None:
        # All seeds failed. Clear the canonical website only if that was one of
        # the seeds; a stale source.url should not wipe the mosque homepage.
        if website_url and website_url in seed_urls:
            with session_scope(engine, write=True) as s:
                repo.update_mosque_website(s, mosque_id, None)
        return DiscoverOutcome(
            mosque_id, "dead", None, detail=(live.error if live else "unreachable")
        )

    homepage_html = fetched.html or "" if fetched and _usable(fetched) else ""

    # Fetch the ordered page set once: homepage, ranked sub-pages, keyword links.
    pages = _page_set(home_url, homepage_html, blocklist)
    fetched_pages: dict[str, str] = {}
    if homepage_html:
        fetched_pages[home_url] = homepage_html
    for url in pages:
        if url in fetched_pages:
            continue
        res = fetcher(url, client=client)
        if not _usable(res):
            continue
        fetched_pages[url] = res.html

    # Detect + verify on every fetched page; keep the best verified result.
    best = _best_verified(
        pages, fetched_pages, today=today, horizon_days=horizon_days, fetcher=fetcher
    )

    # Static miss: re-render the JS-shell pages and verify again, so a site whose
    # timetable is injected by JavaScript is never skipped as "static". Only the
    # pages that actually look JS-hidden are rendered, never the whole corpus.
    requires_js = False
    rendered_pages: dict[str, str] = {}
    if best is None and renderer is not None:
        for url, html in fetched_pages.items():
            if not _page_needs_render(url, html):
                continue
            res = fetcher(url, requires_js=True, renderer=renderer, client=client)
            if _usable(res):
                rendered_pages[url] = res.html
        if rendered_pages:
            rendered = _best_verified(
                list(rendered_pages),
                rendered_pages,
                today=today,
                horizon_days=horizon_days,
                fetcher=fetcher,
            )
            if rendered is not None:
                best = rendered
                requires_js = True

    if best is not None:
        match = best.match
        with session_scope(engine, write=True) as s:
            repo.create_or_update_source(
                s,
                source_id=mosque_id,
                mosque_id=mosque_id,
                url=match.url,
                platform=match.platform,
                shape=match.config.shape,
                config=match.config.to_json(),
                requires_js=match.requires_js or requires_js,
                triage_status="authored",
            )
        result = extract_source(
            engine,
            mosque_id,
            fetcher=fetcher,
            today=today,
            horizon_days=horizon_days,
            renderer=renderer,
            nav_renderer=nav_renderer,
        )
        return DiscoverOutcome(mosque_id, result.triage_status, match.platform)

    # Merge the JS-rendered DOM over the static fetch so EVERY downstream consumer
    # — evidence, the enumerator, the terminal check, and the AI hand-off bundle —
    # sees the timetable the browser injected, not the pre-render shell. Rendering
    # the JS-shell pages was already paid for above (during the static-miss retry);
    # discarding it left the model authoring blind against serialized framework
    # payloads. Pages we rendered are tracked so the bundle/source flag requires_js.
    effective_pages = {**fetched_pages, **rendered_pages}
    rendered_urls = frozenset(rendered_pages)

    # Deterministic miss → build structured evidence once (reused for the
    # enumerator, the terminal check, and the AI hand-off bundle).
    evidences = [
        build_page_evidence(html, url, today=today) for url, html in effective_pages.items()
    ]

    # Build the candidate bundle once we know the effective page set. Saving it
    # before every deterministic exit keeps forced re-authoring from operating
    # against stale evidence (e.g. a source whose timetable lives on a sub-page
    # that the original homepage seed never reached).
    bundle = _bundle_from_pages(
        mosque_id, home_url, effective_pages, evidence=evidences, rendered_urls=rendered_urls
    )
    bundle.save(candidate_root)

    # Deterministic config enumeration: try the obvious configs the evidence implies
    # (media/PDF links, widgets, extra table orientations) and verify them in memory
    # against the already-fetched pages. The platform detectors above cover inline
    # tables; this primarily recovers media-only and widget sources for £0 before
    # the AI funnel. Persist the best verified candidate, if any.
    enum_candidates = enumerate_candidates(evidences)
    if enum_candidates:
        recovered = best_verified_candidate(
            enum_candidates,
            today=today,
            horizon_days=horizon_days,
            fetcher=cached_fetcher(effective_pages, fetcher),
            renderer=renderer,
            nav_renderer=nav_renderer,
        )
        if recovered is not None:
            out = persist_verified_candidate(engine, mosque_id, recovered, authored_by="enumerator")
            return DiscoverOutcome(
                mosque_id,
                out.triage_status,
                recovered.candidate.platform or "enumerator",
            )

    # Conservative terminal classification: if every usable page is conclusively
    # not a timetable (under construction / parked / wrong site / empty) and no
    # page carries any media/widget/iframe/prayer-table/JS evidence, record
    # no_timetable instead of spending an AI call on a source with nothing to author.
    terminal = terminal_no_timetable(evidences)
    if terminal is not None:
        last_status, last_error = terminal
        with session_scope(engine, write=True) as s:
            repo.create_or_update_source(
                s,
                source_id=mosque_id,
                mosque_id=mosque_id,
                url=home_url,
                platform=None,
                shape=None,
                config=None,
                requires_js=False,
                triage_status="no_timetable",
            )
            repo.set_source_state(s, mosque_id, last_status=last_status, last_error=last_error)
        return DiscoverOutcome(mosque_id, "no_timetable", None, detail=last_status)

    # Otherwise hand off to the AI funnel. The bundle carries the rendered DOM for
    # JS pages (so the model sees the real timetable) and flags those candidates
    # requires_js, so the source row — and the model's first verify — render instead
    # of fetching the empty shell.
    top = bundle.candidates[0] if bundle.candidates else None
    with session_scope(engine, write=True) as s:
        repo.create_or_update_source(
            s,
            source_id=mosque_id,
            mosque_id=mosque_id,
            url=top.url if top else None,
            platform=None,
            shape=None,
            config=None,
            requires_js=bool(top and top.requires_js),
            triage_status="candidate",
        )
    return DiscoverOutcome(mosque_id, "candidate", None)


def run_discovery(
    engine,
    *,
    fetcher=fetch,
    client: httpx.Client | None = None,
    candidate_root: Path,
    today: date | None = None,
    horizon_days: int = 60,
    blocklist: frozenset[str] | None = None,
    concurrency: int = 16,
    renderer: Callable[[str], str] | None = None,
    nav_renderer=None,
    force: bool = False,
) -> list[DiscoverOutcome]:
    with session_scope(engine) as s:
        ids = [m.id for m in repo.mosques_for_discovery(s)]

    def _one(mid: str) -> DiscoverOutcome:
        return discover_mosque(
            engine,
            mid,
            fetcher=fetcher,
            client=client,
            candidate_root=candidate_root,
            today=today,
            horizon_days=horizon_days,
            blocklist=blocklist,
            renderer=renderer,
            nav_renderer=nav_renderer,
            force=force,
        )

    # ids come back id-ordered; pool.map preserves order → deterministic results.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(_one, ids))
