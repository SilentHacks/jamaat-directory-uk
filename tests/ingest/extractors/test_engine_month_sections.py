from datetime import date

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import (
    ColumnSpec,
    DateSpec,
    GridSpec,
    SourceConfig,
)
from directory.ingest.extractors.engine import extract

# Azhar-style: separate <td>-only tables per month, each topped by a month
# caption; rows are numbered by day, the month comes from the caption.
SEPARATE_TABLES = (
    "<html><body>"
    "<table>"
    "<tr><td colspan='3'>January</td></tr>"
    "<tr><td rowspan='2'>Day</td><td colspan='2'>Fajr</td></tr>"
    "<tr><td>Begins</td><td>Jamah</td></tr>"
    "<tr><td>1</td><td>06:25 am</td><td>07:00 am</td></tr>"
    "<tr><td>2</td><td>06:24 am</td><td>07:01 am</td></tr>"
    "</table>"
    "<table>"
    "<tr><td colspan='3'>February</td></tr>"
    "<tr><td rowspan='2'>Day</td><td colspan='2'>Fajr</td></tr>"
    "<tr><td>Begins</td><td>Jamah</td></tr>"
    "<tr><td>1</td><td>06:00 am</td><td>06:45 am</td></tr>"
    "</table>"
    "</body></html>"
)

# One big table; full-width month rows reset the month context mid-body.
BIG_TABLE = (
    "<html><body><table>"
    "<tr><td colspan='3'>January</td></tr>"
    "<tr><td>Day</td><td>Begins</td><td>Jamah</td></tr>"
    "<tr><td>1</td><td>06:25 am</td><td>07:00 am</td></tr>"
    "<tr><td colspan='3'>February</td></tr>"
    "<tr><td>Day</td><td>Begins</td><td>Jamah</td></tr>"
    "<tr><td>1</td><td>06:00 am</td><td>06:45 am</td></tr>"
    "</table></body></html>"
)


def _config():
    return SourceConfig(
        shape="html_table",
        grid=GridSpec(
            month_sections=True,
            date=DateSpec(index=0, format="day_only"),
            columns=[
                ColumnSpec(kind="begin", prayer=Prayer.FAJR, index=1),
                ColumnSpec(kind="jamaah", prayer=Prayer.FAJR, index=2),
            ],
        ),
    )


def _by_date(result):
    out = {}
    for c in result.cells:
        out.setdefault(c.date, {})[c.kind] = c.time
    return out


def test_separate_tables_scope_each_month_from_its_caption():
    result = extract(SEPARATE_TABLES, _config(), year=2026, month=1, today=date(2026, 1, 15))
    by_date = _by_date(result)
    assert by_date[date(2026, 1, 1)] == {"begin": "06:25", "jamaah": "07:00"}
    assert by_date[date(2026, 1, 2)] == {"begin": "06:24", "jamaah": "07:01"}
    assert by_date[date(2026, 2, 1)] == {"begin": "06:00", "jamaah": "06:45"}


def test_big_table_month_rows_reset_context():
    result = extract(BIG_TABLE, _config(), year=2026, month=1, today=date(2026, 1, 15))
    by_date = _by_date(result)
    assert by_date[date(2026, 1, 1)]["jamaah"] == "07:00"
    assert by_date[date(2026, 2, 1)]["jamaah"] == "06:45"


def test_month_before_run_month_wraps_to_next_year():
    # Run in December: a January section belongs to next year.
    result = extract(SEPARATE_TABLES, _config(), year=2026, month=12, today=date(2026, 12, 15))
    days = {c.date for c in result.cells}
    assert date(2027, 1, 1) in days  # January wrapped forward
    assert date(2027, 2, 1) in days


def test_caption_element_seeds_month():
    html = (
        "<table><caption>March</caption>"
        "<tr><td>Day</td><td>Begins</td><td>Jamah</td></tr>"
        "<tr><td>1</td><td>05:10 am</td><td>05:40 am</td></tr>"
        "</table>"
    )
    result = extract(html, _config(), year=2026, month=1, today=date(2026, 1, 15))
    assert {c.date for c in result.cells} == {date(2026, 3, 1)}


def test_rows_before_any_month_caption_are_skipped():
    # A stray day row with no preceding month must not be emitted under a guess.
    html = (
        "<table>"
        "<tr><td>1</td><td>05:00 am</td><td>05:30 am</td></tr>"
        "<tr><td colspan='3'>April</td></tr>"
        "<tr><td>2</td><td>05:01 am</td><td>05:31 am</td></tr>"
        "</table>"
    )
    result = extract(html, _config(), year=2026, month=1, today=date(2026, 1, 15))
    days = {c.date for c in result.cells}
    assert days == {date(2026, 4, 2)}  # the pre-caption row 1 is dropped
