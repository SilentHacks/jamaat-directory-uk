from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.extractors.platforms import base as platforms
from directory.ingest.fetch import fetch
from directory.ingest.runner import extract_source

_UA = "jamaat-directory-uk/0.1 (+https://github.com/SilentHacks/jamaat-directory-uk)"


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
        resp = client.get(url, headers={"User-Agent": _UA})
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


def _keyword_links(html: str, base_url: str) -> list[str]:
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
) -> CandidateBundle:
    targets: list[str] = [urljoin(base_url, p) for p in RANKED_PATHS]
    if homepage_html:
        for link in _keyword_links(homepage_html, base_url):
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


def discover_mosque(
    engine,
    mosque_id: str,
    *,
    fetcher=fetch,
    client: httpx.Client | None = None,
    candidate_root: Path,
    today: date | None = None,
    horizon_days: int = 60,
) -> DiscoverOutcome:
    with session_scope(engine) as s:
        mosque = repo.get_mosque(s, mosque_id)
        website = mosque.website_url if mosque else None
    if not website:
        return DiscoverOutcome(mosque_id, "no_website", None)

    live = check_liveness(website, client=client)
    if not live.alive:
        with session_scope(engine) as s:
            repo.update_mosque_website(s, mosque_id, None)
        return DiscoverOutcome(mosque_id, "dead", None, detail=live.error)

    home_url = live.final_url or website
    fetched = fetcher(home_url, client=client)
    homepage_html = fetched.html or ""

    match = platforms.detect_platform(homepage_html, home_url) if homepage_html else None
    if match is not None:
        with session_scope(engine) as s:
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

    # Local import breaks the discover <-> candidate_store cycle: candidate_store
    # imports Candidate/CandidateBundle from this module at import time.
    from directory.ingest.candidate_store import save_bundle

    bundle = gather_candidates(
        mosque_id, home_url, homepage_html=homepage_html, client=client, fetcher=fetcher
    )
    save_bundle(bundle, root=candidate_root)
    best_url = bundle.candidates[0].url if bundle.candidates else None
    with session_scope(engine) as s:
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
        )
        for mid in ids
    ]
