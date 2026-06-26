"""Structural authoring validation (C8): catch configs the pydantic schema accepts
but the engine would silently extract zero rows from, so the funnel can feed the
model an actionable reason instead of a bare 'no occurrences'."""

from directory.ingest.extractors.config_schema import SourceConfig, authoring_problems


def _cfg(d: dict) -> SourceConfig:
    return SourceConfig.model_validate(d)


def test_sound_html_table_has_no_problems():
    cfg = _cfg({
        "shape": "html_table",
        "grid": {
            "date": {"index": 0},
            "columns": [{"kind": "jamaah", "prayer": "fajr", "index": 1}],
        },
    })
    assert authoring_problems(cfg) == []


def test_sound_html_repeated_has_no_problems():
    cfg = _cfg({
        "shape": "html_repeated",
        "grid": {
            "row_selector": "div.day",
            "date": {"selector": ".d"},
            "columns": [{"kind": "jamaah", "prayer": "fajr", "selector": ".f"}],
        },
    })
    assert authoring_problems(cfg) == []


def test_html_table_column_without_index_is_flagged():
    cfg = _cfg({
        "shape": "html_table",
        "grid": {"date": {"index": 0}, "columns": [{"kind": "jamaah", "prayer": "fajr"}]},
    })
    probs = authoring_problems(cfg)
    assert any("index" in p for p in probs)


def test_negative_index_is_flagged():
    cfg = _cfg({
        "shape": "html_table",
        "grid": {
            "date": {"index": 0},
            "columns": [{"kind": "jamaah", "prayer": "fajr", "index": -1}],
        },
    })
    assert any("negative index" in p for p in authoring_problems(cfg))


def test_html_repeated_column_without_selector_is_flagged():
    cfg = _cfg({
        "shape": "html_repeated",
        "grid": {"row_selector": "div.day", "date": {"selector": ".d"},
                 "columns": [{"kind": "jamaah", "prayer": "fajr"}]},
    })
    assert any("selector" in p for p in authoring_problems(cfg))


def test_html_repeated_without_row_selector_is_flagged():
    cfg = _cfg({
        "shape": "html_repeated",
        "grid": {"date": {"selector": ".d"},
                 "columns": [{"kind": "jamaah", "prayer": "fajr", "selector": ".f"}]},
    })
    assert any("row_selector" in p for p in authoring_problems(cfg))


def test_no_columns_is_flagged():
    cfg = _cfg({"shape": "html_table", "grid": {"date": {"index": 0}, "columns": []}})
    assert any("no columns" in p for p in authoring_problems(cfg))


def test_missing_date_axis_is_flagged():
    cfg = _cfg({
        "shape": "html_table",
        "grid": {"columns": [{"kind": "jamaah", "prayer": "fajr", "index": 1}]},
    })
    assert any("date axis" in p for p in authoring_problems(cfg))


def test_single_day_satisfies_date_axis():
    cfg = _cfg({
        "shape": "html_table",
        "grid": {"single_day": True,
                 "columns": [{"kind": "jamaah", "prayer": "fajr", "index": 1}]},
    })
    assert authoring_problems(cfg) == []


def test_paging_satisfies_date_axis():
    cfg = _cfg({
        "shape": "html_table",
        "grid": {"date": {"index": 0},
                 "columns": [{"kind": "jamaah", "prayer": "fajr", "index": 1}]},
        "paging": {"mode": "url_template", "url_template": "https://x/{year}/{month:02d}"},
    })
    assert authoring_problems(cfg) == []


def test_non_grid_shapes_are_exempt():
    assert authoring_problems(_cfg({"shape": "rules", "rules": {"rules": []}})) == []
    assert authoring_problems(
        _cfg({"shape": "widget", "widget": {"platform": "mawaqit"}})
    ) == []
    assert authoring_problems(
        _cfg({"shape": "image", "media": {"url": "https://x/june.jpg"}})
    ) == []
