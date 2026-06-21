import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import httpx

USER_AGENT = "jamaat-directory-uk/0.1 (+https://github.com/SilentHacks/jamaat-directory-uk)"


@dataclass
class FetchResult:
    url: str
    status: int
    html: str | None
    html_hash: str | None
    not_modified: bool = False
    error: str | None = None


def _hash(html: str) -> str:
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
        return FetchResult(url, resp.status_code, html, _hash(html))
    except httpx.HTTPError as exc:
        return FetchResult(url, 0, None, None, error=f"{type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            client.close()


def render_playwright(url: str, *, timeout_ms: int = 15000) -> str:
    # lazy: never imported by default
    from playwright.sync_api import (
        TimeoutError as PlaywrightTimeoutError,
    )
    from playwright.sync_api import (
        sync_playwright,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            # domcontentloaded is reliable; waiting only on networkidle hangs on
            # pages with polling/analytics that never go quiet.
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Best-effort settle so JS-injected tables (e.g. Google Sheets)
            # populate, but never fail when the page never reaches network idle.
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass
            return page.content()
        finally:
            browser.close()
