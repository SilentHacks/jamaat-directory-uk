from pathlib import Path

from directory.ingest.mib import clean_mib_export, write_seed_file
from directory.ingest.seed import load_seed_file

FIXTURE = Path(__file__).parent / "fixtures" / "mib_sample.json"


def test_clean_maps_fields_to_seed_schema():
    records = clean_mib_export(FIXTURE)
    assert len(records) == 2
    first = records[0]
    assert first["id"] == "mib-1"
    assert first["lat"] == 57.1609160759
    assert first["lng"] == -2.1007543802
    assert first["address"] == "164-168 Spital"
    assert first["website_url"] == "http://www.aberdeenmosque.org"


def test_clean_preserves_null_website_and_joins_address():
    records = clean_mib_export(FIXTURE)
    second = records[1]
    assert second["website_url"] is None
    assert second["address"] == "1 High Street, Floor 2"
    assert second["aliases"] == ["Backup Name"]


def test_clean_output_passes_seed_validation(tmp_path):
    records = clean_mib_export(FIXTURE)
    out = write_seed_file(records, tmp_path / "seed" / "mosques.json")
    assert out.exists()
    # The cleaner's output must satisfy the seed importer's schema.
    assert len(load_seed_file(out)) == 2
