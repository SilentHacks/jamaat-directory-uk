from typing import Literal

from pydantic import BaseModel, Field, model_validator

from directory.domain import Prayer

Shape = Literal[
    "html_table", "html_repeated", "rules", "widget", "image", "pdf", "bespoke"
]


class ColumnSpec(BaseModel):
    kind: Literal["jamaah", "begin"]
    prayer: Prayer | None = None  # None → prayer comes from a row label
    index: int | None = None  # html_table: 0-based column index (post-transpose)
    selector: str | None = None  # html_repeated: CSS selector within a row item
    header_seen: str | None = None  # raw header text captured at authoring


class DateSpec(BaseModel):
    index: int | None = None
    selector: str | None = None
    format: str | None = None  # "day_only"|"dd/mm"|"d_month"|"iso"|None (auto)


class GridSpec(BaseModel):
    table_selector: str | None = None  # html_table: CSS for the <table>
    row_selector: str | None = None  # html_repeated: CSS for each day item
    transpose: bool = False
    date: DateSpec | None = None
    columns: list[ColumnSpec] = Field(default_factory=list)


class JumuahSessionSpec(BaseModel):
    label: str
    time: str | None = None  # "HH:MM" when JumuahSpec.source == "fixed"


class JumuahSpec(BaseModel):
    source: Literal["fixed", "table", "rules"] = "fixed"
    sessions: list[JumuahSessionSpec] = Field(default_factory=list)
    seasonal: dict[str, list[JumuahSessionSpec]] | None = None  # "summer"/"winter"


class RuleSpec(BaseModel):
    prayer: Prayer
    fixed: str | None = None  # "HH:MM"
    # NOTE: offset_min is NOT wired in Phase 2. The "rules" shape yields no
    # cells, so no scraped begin time is fed to offset rules; offset resolution
    # is a Phase 3 concern.
    offset_min: int | None = None  # minutes after a scraped begin time


class RulesSpec(BaseModel):
    rules: list[RuleSpec] = Field(default_factory=list)


class SourceConfig(BaseModel):
    shape: Shape
    grid: GridSpec | None = None
    jumuah: JumuahSpec | None = None
    rules: RulesSpec | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "SourceConfig":
        if self.shape in {"html_table", "html_repeated"} and self.grid is None:
            raise ValueError(f"shape {self.shape!r} requires a grid spec")
        if self.shape == "rules" and self.rules is None:
            raise ValueError("shape 'rules' requires a rules spec")
        return self

    @classmethod
    def from_json(cls, raw: str) -> "SourceConfig":
        return cls.model_validate_json(raw)

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)
