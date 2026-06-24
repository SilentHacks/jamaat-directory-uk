import hashlib
import re
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

from directory.ingest.extractors.config_schema import NavSpec

USER_AGENT = "jamaat-directory-uk/0.1 (+https://github.com/SilentHacks/jamaat-directory-uk)"

# Global cap on concurrent headless-browser renders. Discovery runs many workers
# in parallel, but each render_playwright call launches its own chromium; too many
# at once causes render timeouts. This semaphore bounds the live browser count
# (sized from Settings.render_concurrency) while leaving static fetches parallel.
_render_semaphore: threading.BoundedSemaphore | None = None
_render_sem_lock = threading.Lock()


def _render_semaphore_for() -> threading.BoundedSemaphore:
    global _render_semaphore
    if _render_semaphore is None:
        with _render_sem_lock:
            if _render_semaphore is None:
                from directory.config import Settings
                n = max(1, Settings().render_concurrency)
                _render_semaphore = threading.BoundedSemaphore(n)
    return _render_semaphore


@contextmanager
def _render_slot():
    """Hold one of the limited render slots for the duration of a browser session."""
    sem = _render_semaphore_for()
    sem.acquire()
    try:
        yield
    finally:
        sem.release()

# A populated timetable shows several clock times; one stray time (a "next prayer"
# countdown) does not. Matches both "13:30" and dot-separated "13.30".
_CLOCK_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
_RENDER_MIN_CLOCKS = 5


@dataclass
class FetchResult:
    url: str
    status: int
    html: str | None
    html_hash: str | None
    not_modified: bool = False
    error: str | None = None


def html_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()[:16]


def fetch(
    url: str,
    *,
    requires_js: bool = False,
    etag: str | None = None,
    last_modified: str | None = None,
    client: httpx.Client | None = None,
    renderer: Callable[[str], str] | None = None,
    timeout: float = 20.0,
) -> FetchResult:
    headers = {"User-Agent": USER_AGENT}
    if etag is not None:
        headers["If-None-Match"] = etag
    if last_modified is not None:
        headers["If-Modified-Since"] = last_modified

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url, headers=headers)
        if resp.status_code == 304:
            return FetchResult(url, 304, None, None, not_modified=True)
        html = resp.text
        if requires_js and renderer is not None:
            try:
                html = renderer(url)
            except Exception as exc:
                # The renderer is an external browser (Playwright); a render
                # timeout or crash must skip the page, never abort discovery.
                return FetchResult(
                    url, resp.status_code, None, None,
                    error=f"render failed: {type(exc).__name__}: {exc}",
                )
        return FetchResult(url, resp.status_code, html, html_hash(html))
    except httpx.HTTPError as exc:
        return FetchResult(url, 0, None, None, error=f"{type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            client.close()


def _goto_and_settle(page, url: str, timeout_ms: int) -> None:
    """Navigate and best-effort settle. domcontentloaded is reliable; waiting only
    on networkidle hangs on pages with polling/analytics that never go quiet, so
    networkidle is awaited but never allowed to fail."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass


def _await_step(page, nav: NavSpec, timeout_ms: int) -> None:
    """After a navigation step, wait for the freshly loaded month to appear: the
    configured ready_selector if given, else a fixed settle."""
    if nav.ready_selector:
        try:
            page.wait_for_selector(nav.ready_selector, timeout=timeout_ms)
        except Exception:
            pass
    else:
        page.wait_for_timeout(nav.settle_ms)


def _timetable_ready(html: str, *, min_clocks: int = _RENDER_MIN_CLOCKS) -> bool:
    """A rendered page carries a populated timetable once it shows several clock
    times — the signal that a JS-injected table (Google Sheets, prayer widgets)
    has hydrated rather than being a bare shell."""
    return len(_CLOCK_RE.findall(html)) >= min_clocks


def _settle_for_timetable(
    page, settle_ms: int, *, poll_ms: int = 400, clock: Callable[[], float] = time.monotonic
) -> str:
    """Poll the live DOM until it shows a timetable's worth of clock times, then
    return its HTML. JS timetables routinely populate *after* networkidle, so
    snapshotting immediately races the hydration — a race that loses far more
    often under concurrent renders (CPU contention) and yields a shell with no
    rows. Best-effort: a page that genuinely has few times just returns its
    content once the deadline passes, never raising."""
    html = page.content()
    if _timetable_ready(html):
        return html
    deadline = clock() + settle_ms / 1000
    while clock() < deadline:
        page.wait_for_timeout(poll_ms)
        html = page.content()
        if _timetable_ready(html):
            break
    return html


def render_playwright(url: str, *, timeout_ms: int = 15000, settle_ms: int = 8000) -> str:
    # lazy: never imported by default
    from playwright.sync_api import sync_playwright

    with _render_slot(), sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            # Settle on the network, then wait for the timetable itself to hydrate
            # before snapshotting — domcontentloaded/networkidle alone races the
            # JS that injects the prayer rows.
            _goto_and_settle(page, url, timeout_ms)
            return _settle_for_timetable(page, settle_ms)
        finally:
            browser.close()


def _step_to_month(page, nav: NavSpec, year: int, month: int) -> None:
    """Advance the calendar one step toward ``(year, month)``.

    kind="next": click the forward control once (relative; the caller drives the
    months in order). kind="select": pick the target month by display order
    (Jan=index 0) and the year by its visible text — covers the common ordered
    month dropdown without depending on site-specific option ``value`` schemes.
    """
    if nav.kind == "select":
        page.select_option(nav.month_select, index=month - 1)
        if nav.year_select:
            page.select_option(nav.year_select, label=str(year))
    else:
        page.click(nav.next_selector)


def render_playwright_nav(
    url: str, nav: NavSpec, months: list[tuple[int, int]], *, timeout_ms: int = 15000
) -> list[str]:
    """Drive a JS calendar to each month in ``months`` and capture its HTML.

    The page loads on the current month (months[0]); each later month is reached
    by a navigation step. A step that raises stops the walk and returns the HTML
    gathered so far — partial months are tolerated upstream, never fatal."""
    from playwright.sync_api import sync_playwright

    with _render_slot(), sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            _goto_and_settle(page, url, timeout_ms)
            htmls = [page.content()]  # months[0] is shown on load
            for year, month in months[1:]:
                try:
                    _step_to_month(page, nav, year, month)
                    _await_step(page, nav, timeout_ms)
                    htmls.append(page.content())
                except Exception:
                    break
            return htmls
        finally:
            browser.close()
