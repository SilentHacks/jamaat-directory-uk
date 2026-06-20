import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import httpx

_UA = "jamaat-directory-uk/0.1 (+https://github.com/SilentHacks/jamaat-directory-uk)"


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
    headers = {"User-Agent": _UA}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url, headers=headers)
        if resp.status_code == 304:
            return FetchResult(url, 304, None, None, not_modified=True)
        html = resp.text
        if requires_js and renderer is not None:
            html = renderer(url)
        return FetchResult(url, resp.status_code, html, _hash(html))
    except httpx.HTTPError as exc:
        return FetchResult(url, 0, None, None, error=f"{type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            client.close()


def render_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright  # lazy: never imported by default

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            return page.content()
        finally:
            browser.close()
