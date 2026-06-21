import json
from pathlib import Path

from sqlalchemy import Engine

from directory import repository as repo
from directory.db import session_scope

_REQUIRED = ("id", "name", "lat", "lng")


def _address(record: dict) -> str | None:
    parts = [record.get("address_line1"), record.get("address_line2")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def clean_mib_export(raw_path: Path) -> list[dict]:
    """Map a raw MuslimsInBritain export into the seed-file schema."""
    raw = json.loads(Path(raw_path).read_text())
    cleaned: list[dict] = []
    for m in raw.get("mosques", []):
        cleaned.append(
            {
                "id": m["external_id"],
                "name": m["name"],
                "aliases": m.get("aliases") or [],
                "address": _address(m),
                "city": m.get("city"),
                "postcode": m.get("postcode"),
                "country": m.get("country", "GB"),
                "lat": m["latitude"],
                "lng": m["longitude"],
                "website_url": m.get("website_url"),
            }
        )
    return cleaned


def write_seed_file(records: list[dict], out_path: Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    return out


def load_seed_file(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("seed file must be a JSON array")
    for i, record in enumerate(data):
        missing = [k for k in _REQUIRED if record.get(k) is None]
        if missing:
            raise ValueError(f"record {i} missing required field(s): {missing}")
    return data


def seed_database(engine: Engine, mosques: list[dict]) -> int:
    with session_scope(engine) as s:
        return repo.upsert_mosques(s, mosques)
