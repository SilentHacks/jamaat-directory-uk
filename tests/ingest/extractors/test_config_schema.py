import pytest

from directory.domain import Prayer
from directory.ingest.extractors.config_schema import NavSpec, PagingSpec, SourceConfig


def test_html_table_roundtrips_through_json():
    raw = """
    {
      "shape": "html_table",
      "grid": {
        "table_selector": "table.times",
        "transpose": false,
        "date": {"index": 0, "format": "day_only"},
        "columns": [
          {"kind": "begin", "prayer": "fajr", "index": 1, "header_seen": "Fajr Begins"},
          {"kind": "jamaah", "prayer": "fajr", "index": 2, "header_seen": "Fajr Iqamah"}
        ]
      }
    }
    """
    cfg = SourceConfig.from_json(raw)
    assert cfg.shape == "html_table"
    assert cfg.grid.columns[0].prayer == Prayer.FAJR
    assert cfg.grid.columns[1].kind == "jamaah"
    # to_json drops null fields and re-parses identically
    again = SourceConfig.from_json(cfg.to_json())
    assert again.grid.date.index == 0


def test_jumuah_fixed_sessions_parse():
    cfg = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":[]},'
        '"jumuah":{"source":"fixed","sessions":['
        '{"label":"1st Jumu\\u2019ah","time":"13:00"},'
        '{"label":"2nd Jumu\\u2019ah","time":"13:45"}]}}'
    )
    assert cfg.jumuah.source == "fixed"
    assert [s.time for s in cfg.jumuah.sessions] == ["13:00", "13:45"]


def test_html_table_without_grid_is_rejected():
    with pytest.raises(ValueError):
        SourceConfig.from_json('{"shape":"html_table"}')


def test_column_value_kind_defaults_to_none_and_stored_config_roundtrips_identically():
    # A pre-existing stored config (itself produced by to_json) has no
    # value_kind / base_prayer. Defaulting both to None means a re-serialize is
    # byte-identical and the new fields never appear — no DB churn, no re-author.
    stored = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":['
        '{"kind":"jamaah","prayer":"fajr","index":1}]}}'
    ).to_json()
    col = SourceConfig.from_json(stored).grid.columns[0]
    assert col.value_kind is None  # None == "time"
    assert col.base_prayer is None
    assert SourceConfig.from_json(stored).to_json() == stored
    assert "value_kind" not in stored and "base_prayer" not in stored


def test_offset_column_with_base_prayer_parses():
    cfg = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":['
        '{"kind":"jamaah","prayer":"isha","index":3,'
        '"value_kind":"offset","base_prayer":"maghrib"}]}}'
    )
    col = cfg.grid.columns[0]
    assert col.value_kind == "offset"
    assert col.base_prayer == Prayer.MAGHRIB


def test_vertical_single_day_grid_parses_and_defaults_are_absent():
    # Prayer-rows layout: a label column names prayers, header names kinds, no
    # date axis. prayer_label_index selects orientation; single_day stamps today.
    cfg = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"prayer_label_index":0,"single_day":true,'
        '"columns":[{"kind":"begin","index":1},{"kind":"jamaah","index":2}]}}'
    )
    assert cfg.grid.prayer_label_index == 0
    assert cfg.grid.single_day is True
    assert cfg.grid.columns[0].prayer is None  # prayer comes from the row label

    # Defaults stay absent/false so existing stored configs are byte-identical.
    stored = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":['
        '{"kind":"jamaah","prayer":"fajr","index":1}]}}'
    ).to_json()
    assert "prayer_label_index" not in stored
    assert "single_day" not in stored


def test_rules_shape_requires_rules_block():
    with pytest.raises(ValueError):
        SourceConfig.from_json('{"shape":"rules"}')
    ok = SourceConfig.from_json(
        '{"shape":"rules","rules":{"rules":[{"prayer":"dhuhr","fixed":"13:30"}]}}'
    )
    assert ok.rules.rules[0].prayer == Prayer.DHUHR


def test_paging_url_template_parses_and_roundtrips():
    cfg = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":['
        '{"kind":"jamaah","prayer":"fajr","index":1}]},'
        '"paging":{"mode":"url_template","url_template":"https://x.org/{year}/{month:02d}"}}'
    )
    assert cfg.paging.mode == "url_template"
    assert cfg.paging.url_template == "https://x.org/{year}/{month:02d}"
    assert SourceConfig.from_json(cfg.to_json()).paging.url_template == cfg.paging.url_template


def test_paging_render_nav_parses():
    cfg = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":['
        '{"kind":"jamaah","prayer":"fajr","index":1}]},'
        '"paging":{"mode":"render_nav","nav":{"kind":"next",'
        '"next_selector":".cal-next","ready_selector":".cal-grid"}}}'
    )
    assert cfg.paging.mode == "render_nav"
    assert cfg.paging.nav.kind == "next"
    assert cfg.paging.nav.next_selector == ".cal-next"
    assert cfg.paging.nav.settle_ms == 800  # default


def test_paging_default_absent_for_legacy_configs():
    # An existing stored config never grows a paging key → no DB churn.
    stored = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":['
        '{"kind":"jamaah","prayer":"fajr","index":1}]}}'
    ).to_json()
    assert SourceConfig.from_json(stored).paging is None
    assert "paging" not in stored


def test_paging_url_template_requires_template():
    with pytest.raises(ValueError):
        PagingSpec.model_validate({"mode": "url_template"})


def test_paging_render_nav_requires_nav():
    with pytest.raises(ValueError):
        PagingSpec.model_validate({"mode": "render_nav"})


def test_nav_next_requires_next_selector():
    with pytest.raises(ValueError):
        NavSpec.model_validate({"kind": "next"})


def test_nav_select_requires_month_select():
    with pytest.raises(ValueError):
        NavSpec.model_validate({"kind": "select"})
    ok = NavSpec.model_validate({"kind": "select", "month_select": "#month"})
    assert ok.month_select == "#month"
