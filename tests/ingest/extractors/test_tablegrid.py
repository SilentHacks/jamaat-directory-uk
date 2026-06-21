from bs4 import BeautifulSoup

from directory.ingest.extractors.tablegrid import (
    combined_header,
    grid_matrix,
    header_depth,
)


def _table(html: str):
    return BeautifulSoup(html, "lxml").find("table")


FLAT = (
    "<table>"
    "<tr><th>Date</th><th>Fajr</th></tr>"
    "<tr><td>1 Jun</td><td>05:00</td></tr>"
    "</table>"
)


def test_flat_table_matches_naive_flatten():
    t = _table(FLAT)
    assert grid_matrix(t) == [["Date", "Fajr"], ["1 Jun", "05:00"]]
    assert header_depth(t) == 1


GROUPED = """<table><thead>
 <tr><th></th><th></th><th colspan="3">Fajr</th><th colspan="2">Zuhr</th></tr>
 <tr><th>Date</th><th>Day</th><th>Begins</th><th>Jamaah</th><th>Sunrise</th>
     <th>Begins</th><th>Jamaah</th></tr>
</thead><tbody>
 <tr><td>1 Jun</td><td>Mon</td><td>02:50</td><td>03:15</td><td>04:30</td>
     <td>13:00</td><td>13:30</td></tr>
</tbody></table>"""


def test_colspan_expands_across_logical_columns():
    g = grid_matrix(_table(GROUPED))
    assert g[0] == ["", "", "Fajr", "Fajr", "Fajr", "Zuhr", "Zuhr"]
    assert g[1] == ["Date", "Day", "Begins", "Jamaah", "Sunrise", "Begins", "Jamaah"]
    assert g[2][0] == "1 Jun"


def test_header_depth_counts_thead_rows():
    assert header_depth(_table(GROUPED)) == 2


def test_combined_header_joins_group_and_sub_label():
    t = _table(GROUPED)
    h = combined_header(grid_matrix(t), header_depth(t))
    assert h == [
        "Date", "Day", "Fajr Begins", "Fajr Jamaah", "Fajr Sunrise",
        "Zuhr Begins", "Zuhr Jamaah",
    ]


ROWSPAN = """<table>
 <tr><th rowspan="2">Date</th><th colspan="2">Fajr</th></tr>
 <tr><th>Begins</th><th>Jamaah</th></tr>
 <tr><td>1 Jun</td><td>02:50</td><td>03:15</td></tr>
</table>"""


def test_rowspan_carries_down_and_dedupes_in_combined_header():
    t = _table(ROWSPAN)
    g = grid_matrix(t)
    assert g[0] == ["Date", "Fajr", "Fajr"]
    assert g[1] == ["Date", "Begins", "Jamaah"]  # Date carried down by rowspan
    assert header_depth(t) == 2  # no <thead>; two leading all-<th> rows
    assert combined_header(g, 2) == ["Date", "Fajr Begins", "Fajr Jamaah"]
