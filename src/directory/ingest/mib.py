import json
from pathlib import Path


def _address(record: dict) -> str | None:
    parts = [record.get("address_line1"), record.get("address_line2")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def clean_mib_export(raw_path: Path) -> list[dict]:
    raw = json.loads(Path(raw_path).read_text())
    mosques = raw.get("mosques", [])
    cleaned: list[dict] = []
    for m in mosques:
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
