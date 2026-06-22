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


# Azhar-style: a multi-row header built entirely from <td> (no <thead>, no <th>),
# topped by a month-caption row. header_depth must infer the header by content —
# the leading run of time-less rows — since the all-<th> rule yields nothing.
TD_GROUPED = (
    "<table>"
    "<tr><td colspan='5'>January</td></tr>"
    "<tr><td rowspan='2'>Day</td><td colspan='2'>Fajr</td><td colspan='2'>Zuhr</td></tr>"
    "<tr><td>Begins</td><td>Jamah</td><td>Begins</td><td>Jamah</td></tr>"
    "<tr><td>1</td><td>05:00</td><td>05:30</td><td>13:00</td><td>13:30</td></tr>"
    "</table>"
)


def test_td_only_multirow_header_inferred_by_content():
    t = _table(TD_GROUPED)
    assert header_depth(t) == 3  # caption + prayer row + Begins/Jamah row
    header = combined_header(grid_matrix(t), 3)
    assert header[1] == "January Fajr Begins"
    assert header[3] == "January Zuhr Begins"


TD_FLAT = (
    "<table>"
    "<tr><td>Date</td><td>Fajr</td><td>Zuhr</td></tr>"
    "<tr><td>1 Jun</td><td>05:00</td><td>13:00</td></tr>"
    "</table>"
)


def test_td_only_single_header_still_depth_one():
    # No <th>, single header row, multi-day body → inference must stay at 1.
    assert header_depth(_table(TD_FLAT)) == 1


TD_VERTICAL_SINGLE_TIME = (
    "<table>"
    "<tr><td>Waqt</td><td>Iqamah</td></tr>"
    "<tr><td>Fajr</td><td>03:45 am</td></tr>"
    "<tr><td>Zuhr</td><td>01:30 pm</td></tr>"
    "</table>"
)


def test_td_only_vertical_single_time_column_depth_one():
    # One time per row must not drag every row into the header: the first timed
    # row begins the body, so depth is 1 (the Waqt/Iqamah label row).
    assert header_depth(_table(TD_VERTICAL_SINGLE_TIME)) == 1
