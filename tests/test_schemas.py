from directory.models import Mosque, Occurrence
from directory.schemas import MosqueOut, build_day_times


def test_mosque_out_from_model():
    m = Mosque(id="m1", name="A", aliases='["X"]', city="Leicester",
               lat=1.0, lng=2.0, website_url=None, status="active")
    out = MosqueOut.from_model(m, has_times=False)
    assert out.id == "m1"
    assert out.aliases == ["X"]
    assert out.website_url is None
    assert out.has_times is False


def test_build_day_times_scalars_and_jumuah_array():
    occ = [
        Occurrence(mosque_id="m1", date="2026-06-19", prayer="fajr",
                   session_idx=0, jamaah_time="05:00", begin_time="04:45"),
        Occurrence(mosque_id="m1", date="2026-06-19", prayer="dhuhr",
                   session_idx=0, jamaah_time="13:30"),
        Occurrence(mosque_id="m1", date="2026-06-19", prayer="jumuah",
                   session_idx=1, jamaah_time="13:00", label="1st"),
        Occurrence(mosque_id="m1", date="2026-06-19", prayer="jumuah",
                   session_idx=2, jamaah_time="13:45", label="2nd"),
    ]
    dt = build_day_times("2026-06-19", occ)
    assert dt.fajr == "05:00"
    assert dt.dhuhr == "13:30"
    assert dt.asr is None
    assert dt.begin == {"fajr": "04:45"}
    assert [s.time for s in dt.jumuah] == ["13:00", "13:45"]
    assert [s.label for s in dt.jumuah] == ["1st", "2nd"]


def test_build_day_times_empty_is_jumuah_array_not_null():
    dt = build_day_times("2026-06-19", [])
    assert dt.jumuah == []
    assert dt.fajr is None
    assert dt.begin is None
