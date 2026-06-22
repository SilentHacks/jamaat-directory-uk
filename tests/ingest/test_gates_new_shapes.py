"""Lint coverage for the non-<table> shapes added for div-based layouts."""

from directory.ingest.extractors.config_schema import (
    ColumnSpec,
    DateSpec,
    GridSpec,
    NavSpec,
    PagingSpec,
    SourceConfig,
)
from directory.ingest.gates import lint_config


def test_dom_records_empty_grid_is_not_a_lint_error():
    # Columns are induced from the stream at extract time, so none are configured.
    cfg = SourceConfig(shape="dom_records", grid=GridSpec(date=DateSpec(format="d_month")))
    assert lint_config(cfg) == []


def test_dom_records_with_render_nav_paging_lints_clean():
    cfg = SourceConfig(
        shape="dom_records",
        grid=GridSpec(date=DateSpec(format="d_month")),
        paging=PagingSpec(mode="render_nav", nav=NavSpec(kind="next", next_selector="text=›")),
    )
    assert lint_config(cfg) == []


def test_dom_grid_is_linted_like_a_table():
    cfg = SourceConfig(
        shape="html_table",
        grid=GridSpec(
            dom_grid=True,
            date=DateSpec(index=0),
            columns=[ColumnSpec(kind="jamaah", prayer="fajr", index=1)],
        ),
    )
    assert lint_config(cfg) == []

    bad = SourceConfig(shape="html_table", grid=GridSpec(dom_grid=True, columns=[]))
    assert lint_config(bad)  # an empty grid is still a defect for a grid shape
