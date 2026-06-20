from datetime import date

from directory.ingest.extractors.config_schema import (
    JumuahSessionSpec,
    JumuahSpec,
    RuleSpec,
    RulesSpec,
    SourceConfig,
)
from directory.ingest.extractors.engine import ExtractionResult
from directory.ingest.materialize import (
    materialize,
    materialize_jumuah,
    materialize_rules,
)

# 2026-06-05, 2026-06-12, 2026-06-19, 2026-06-26 are Fridays.


def test_jumuah_materialises_each_friday():
    spec = JumuahSpec(
        source="fixed",
        sessions=[
            JumuahSessionSpec(label="1st Jumu'ah", time="13:00"),
            JumuahSessionSpec(label="2nd Jumu'ah", time="13:45"),
        ],
    )
    rows = materialize_jumuah(spec, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 30))
    fridays = {r.date for r in rows}
    assert fridays == {"2026-06-05", "2026-06-12", "2026-06-19", "2026-06-26"}
    first = sorted((r for r in rows if r.date == "2026-06-05"), key=lambda x: x.session_idx)
    assert [(r.session_idx, r.jamaah_time, r.label) for r in first] == [
        (1, "13:00", "1st Jumu'ah"),
        (2, "13:45", "2nd Jumu'ah"),
    ]


def test_jumuah_seasonal_picks_summer_in_june():
    spec = JumuahSpec(
        source="fixed",
        seasonal={
            "summer": [JumuahSessionSpec(label="Jumu'ah", time="13:30")],
            "winter": [JumuahSessionSpec(label="Jumu'ah", time="12:30")],
        },
    )
    rows = materialize_jumuah(spec, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 12))
    assert all(r.jamaah_time == "13:30" for r in rows)


def test_fixed_rule_emits_daily():
    spec = RulesSpec(rules=[RuleSpec(prayer="dhuhr", fixed="13:30")])
    rows = materialize_rules(spec, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 3))
    assert {r.date for r in rows} == {"2026-06-01", "2026-06-02", "2026-06-03"}
    assert all(r.jamaah_time == "13:30" and r.prayer == "dhuhr" for r in rows)


def test_offset_rule_without_begin_is_skipped():
    spec = RulesSpec(rules=[RuleSpec(prayer="asr", offset_min=15)])
    rows = materialize_rules(spec, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 2))
    assert rows == []


def test_top_level_materialize_combines_grid_and_jumuah():
    cfg = SourceConfig.from_json(
        '{"shape":"html_table","grid":{"columns":[]},'
        '"jumuah":{"source":"fixed","sessions":[{"label":"Jumu\\u2019ah","time":"13:15"}]}}'
    )
    rows = materialize(
        ExtractionResult(), cfg, horizon_start=date(2026, 6, 1), horizon_end=date(2026, 6, 12)
    )
    assert all(r.prayer == "jumuah" for r in rows)
    assert {r.date for r in rows} == {"2026-06-05", "2026-06-12"}
