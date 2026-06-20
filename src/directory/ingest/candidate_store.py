import json
from dataclasses import asdict
from pathlib import Path

from directory.ingest.discover import Candidate, CandidateBundle


def save_bundle(bundle: CandidateBundle, *, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{bundle.mosque_id}.json"
    path.write_text(json.dumps(asdict(bundle), ensure_ascii=False), encoding="utf-8")
    return path


def load_bundle(mosque_id: str, *, root: Path) -> CandidateBundle | None:
    path = root / f"{mosque_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return CandidateBundle(
        mosque_id=data["mosque_id"],
        base_url=data["base_url"],
        candidates=[Candidate(**c) for c in data["candidates"]],
    )
