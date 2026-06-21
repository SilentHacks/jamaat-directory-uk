import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.blocklist import is_blocklisted
from directory.ingest.extractors.engine import extract
from directory.ingest.extractors.platforms import base as platforms
from directory.ingest.fetch import USER_AGENT, fetch
from directory.ingest.gates import run_gates
from directory.ingest.materialize import materialize
from directory.ingest.runner import extract_source


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
    "/timetable",
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


@dataclass
class CandidateBundle:
    mosque_id: str
    base_url: str
    candidates: list["Candidate"]

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


def strip_to_region(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
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
        if res.error or not res.html:
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


def _page_set(
    home_url: str, homepage_html: str, blocklist: frozenset[str] | None
) -> list[str]:
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


def _verify(html: str, match, *, today: date | None, horizon_days: int):
    """Run the engine + gates on a detected page without persisting; returns the
    GateResult and a completeness score, or None when the page does not verify."""
    today = today or date.today()
    config = match.config
    result = extract(html, config, year=today.year, month=today.month)
    rows = materialize(
        result, config, horizon_start=today, horizon_end=today + timedelta(days=horizon_days)
    )
    gate = run_gates(config, result, rows, html_text=html)
    if gate.lane not in _LANE_RANK:
        return None
    return gate, len(rows)


def _best_verified(
    pages: list[str], fetched_pages: dict[str, str], *, today: date | None, horizon_days: int
) -> _Verified | None:
    """Pick the best verified page: auto_accept ≻ review, platform-specific ≻
    generic, more-complete ≻ less, earlier page ≻ later."""
    best: _Verified | None = None
    for idx, url in enumerate(pages):
        html = fetched_pages.get(url)
        if not html:
            continue
        match = platforms.detect_platform(html, url)
        if match is None:
            continue
        verified = _verify(html, match, today=today, horizon_days=horizon_days)
        if verified is None:
            continue
        gate, completeness = verified
        is_specific = 0 if match.platform == "generic_table" else 1
        rank = (_LANE_RANK[gate.lane], is_specific, completeness, -idx)
        if best is None or rank > best.rank:
            best = _Verified(match=match, rank=rank)
    return best


def _bundle_from_pages(
    mosque_id: str, base_url: str, fetched_pages: dict[str, str], *, max_candidates: int = 5
) -> CandidateBundle:
    """Build a CandidateBundle from pages already fetched during discovery, so the
    AI hand-off costs no additional requests."""
    candidates: list[Candidate] = []
    for url, html in fetched_pages.items():
        region, text = strip_to_region(html)
        candidates.append(Candidate(url=url, score=_score(text), region_html=region, text=text))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return CandidateBundle(mosque_id, base_url, candidates[:max_candidates])


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
) -> DiscoverOutcome:
    with session_scope(engine) as s:
        mosque = repo.get_mosque(s, mosque_id)
        website = mosque.website_url if mosque else None
    if not website:
        return DiscoverOutcome(mosque_id, "no_website", None)

    live = check_liveness(website, client=client)
    if not live.alive:
        with session_scope(engine, write=True) as s:
            repo.update_mosque_website(s, mosque_id, None)
        return DiscoverOutcome(mosque_id, "dead", None, detail=live.error)

    home_url = live.final_url or website

    # Dead-end the resolved host if it is a social/aggregator/maps domain: no
    # fetch, no AI. Checks the *resolved* URL so redirects to a dead host count.
    if is_blocklisted(home_url, blocklist=blocklist):
        with session_scope(engine, write=True) as s:
            repo.create_or_update_source(
                s, source_id=mosque_id, mosque_id=mosque_id, url=home_url,
                platform=None, shape=None, config=None, requires_js=False,
                triage_status="blocklisted",
            )
        return DiscoverOutcome(mosque_id, "blocklisted", None)

    fetched = fetcher(home_url, client=client)
    homepage_html = fetched.html or ""

    # Fetch the ordered page set once: homepage, ranked sub-pages, keyword links.
    pages = _page_set(home_url, homepage_html, blocklist)
    fetched_pages: dict[str, str] = {}
    if homepage_html:
        fetched_pages[home_url] = homepage_html
    for url in pages:
        if url in fetched_pages:
            continue
        res = fetcher(url, client=client)
        if res.error or not res.html:
            continue
        fetched_pages[url] = res.html

    # Detect + verify on every fetched page; keep the best verified result.
    best = _best_verified(pages, fetched_pages, today=today, horizon_days=horizon_days)
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
                requires_js=match.requires_js,
                triage_status="authored",
            )
        result = extract_source(
            engine, mosque_id, fetcher=fetcher, today=today, horizon_days=horizon_days
        )
        return DiscoverOutcome(mosque_id, result.triage_status, match.platform)

    # Nothing verified deterministically → hand off to the AI funnel.
    bundle = _bundle_from_pages(mosque_id, home_url, fetched_pages)
    bundle.save(candidate_root)
    best_url = bundle.candidates[0].url if bundle.candidates else None
    with session_scope(engine, write=True) as s:
        repo.create_or_update_source(
            s,
            source_id=mosque_id,
            mosque_id=mosque_id,
            url=best_url,
            platform=None,
            shape=None,
            config=None,
            requires_js=False,
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
) -> list[DiscoverOutcome]:
    with session_scope(engine) as s:
        ids = [m.id for m in repo.mosques_for_discovery(s)]
    return [
        discover_mosque(
            engine,
            mid,
            fetcher=fetcher,
            client=client,
            candidate_root=candidate_root,
            today=today,
            horizon_days=horizon_days,
            blocklist=blocklist,
        )
        for mid in ids
    ]
