from datetime import date

import httpx

from directory.db import init_db, make_engine, session_scope
from directory.ingest.discover import run_discovery
from directory.ingest.fetch import FetchResult
from directory.models import Mosque, Occurrence

WP_HTML = """
<html><body>
<table class="dpt_table">
  <tr><th>Date</th><th>Fajr</th><th>Dhuhr</th><th>Asr</th><th>Maghrib</th><th>Isha</th></tr>
  <tr><td>1</td><td>05:00</td><td>13:15</td><td>18:30</td><td>21:10</td><td>22:30</td></tr>
  <tr><td>2</td><td>05:02</td><td>13:16</td><td>18:31</td><td>21:11</td><td>22:31</td></tr>
  <tr><td>3</td><td>05:03</td><td>13:17</td><td>18:32</td><td>21:12</td><td>22:32</td></tr>
</table></body></html>
"""

BLOCKED_HOME = '<html><body>nothing here</body></html>'


def _fetcher(url, *, requires_js=False, etag=None, last_modified=None, client=None,
             renderer=None, timeout=20.0):
    # every live mosque homepage serves the WP table
    return FetchResult(url, 200, WP_HTML, "hash")


def _seed(engine, n):
    with session_scope(engine) as s:
        for i in range(n):
            s.add(Mosque(id=f"m{i:03d}", name=f"M{i}", lat=51.0, lng=-1.0,
                         website_url=f"https://m{i:03d}.example/"))


def _client():
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=True,
    )


def _fresh_engine(tmp_path, name, n):
    eng = make_engine(f"sqlite:///{tmp_path/name}")
    init_db(eng)
    _seed(eng, n)
    return eng


def _run(engine, tmp_path, concurrency):
    return run_discovery(engine, fetcher=_fetcher, client=_client(), candidate_root=tmp_path,
                         today=date(2026, 6, 1), horizon_days=10, concurrency=concurrency)


def test_pool_size_does_not_change_outcomes(tmp_path):
    eng1 = _fresh_engine(tmp_path, "serial.db", 25)
    eng16 = _fresh_engine(tmp_path, "parallel.db", 25)
    serial = _run(eng1, tmp_path, 1)
    parallel = _run(eng16, tmp_path, 16)

    assert len(serial) == 25 == len(parallel)
    assert [o.mosque_id for o in serial] == [o.mosque_id for o in parallel]
    assert [o.outcome for o in serial] == [o.outcome for o in parallel]


def test_all_items_processed_and_rows_written(engine, tmp_path):
    _seed(engine, 30)
    outcomes = _run(engine, tmp_path, 16)
    assert {o.outcome for o in outcomes} == {"authored"}
    with session_scope(engine) as s:
        # 30 mosques * 5 daily prayers * 3 dates = 450 occurrences
        count = s.query(Occurrence).count()
    assert count == 30 * 5 * 3
