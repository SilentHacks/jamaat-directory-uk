from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from directory import repository as repo
from directory.db import session_scope
from directory.ingest.discover import check_liveness


@dataclass
class ValidationSummary:
    checked: int = 0
    repaired: int = 0
    dropped: int = 0
    unchanged: int = 0


def _validate_one(engine, mosque_id: str, url: str, client: httpx.Client | None) -> str:
    res = check_liveness(url, client=client)
    if not res.alive:
        with session_scope(engine, write=True) as s:
            repo.update_mosque_website(s, mosque_id, None)
        return "dropped"
    if res.final_url and res.final_url != url:
        with session_scope(engine, write=True) as s:
            repo.update_mosque_website(s, mosque_id, res.final_url)
        return "repaired"
    return "unchanged"


def validate_websites(
    engine, *, client: httpx.Client | None = None, concurrency: int = 16
) -> ValidationSummary:
    with session_scope(engine) as s:
        targets = [(m.id, m.website_url) for m in repo.mosques_with_website(s)]

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(
            pool.map(lambda t: _validate_one(engine, t[0], t[1], client), targets)
        )

    summary = ValidationSummary(checked=len(targets))
    for r in results:
        setattr(summary, r, getattr(summary, r) + 1)
    return summary
