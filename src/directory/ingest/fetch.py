import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from directory.ingest.extractors.config_schema import NavSpec

USER_AGENT = "jamaat-directory-uk/0.1 (+https://github.com/SilentHacks/jamaat-directory-uk)"


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


def render_playwright(url: str, *, timeout_ms: int = 15000) -> str:
    # lazy: never imported by default
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            # Best-effort settle so JS-injected tables (e.g. Google Sheets)
            # populate, but never fail when the page never reaches network idle.
            _goto_and_settle(page, url, timeout_ms)
            return page.content()
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

    with sync_playwright() as p:
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
