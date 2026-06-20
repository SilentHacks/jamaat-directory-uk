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


def validate_websites(engine, *, client: httpx.Client | None = None) -> ValidationSummary:
    with session_scope(engine) as s:
        targets = [(m.id, m.website_url) for m in repo.mosques_with_website(s)]

    summary = ValidationSummary()
    for mosque_id, url in targets:
        summary.checked += 1
        res = check_liveness(url, client=client)
        if not res.alive:
            with session_scope(engine) as s:
                repo.update_mosque_website(s, mosque_id, None)
            summary.dropped += 1
        elif res.final_url and res.final_url != url:
            with session_scope(engine) as s:
                repo.update_mosque_website(s, mosque_id, res.final_url)
            summary.repaired += 1
        else:
            summary.unchanged += 1
    return summary
