from dataclasses import dataclass

import httpx

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
