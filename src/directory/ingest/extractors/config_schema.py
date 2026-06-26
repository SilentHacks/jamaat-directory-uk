from typing import Literal

from pydantic import BaseModel, Field, model_validator

from directory.domain import Prayer

Shape = Literal[
    "html_table", "html_repeated", "dom_records", "rules", "widget", "image", "pdf",
    "bespoke",
]


class ColumnSpec(BaseModel):
    kind: Literal["jamaah", "begin"]
    prayer: Prayer | None = None  # None → prayer comes from a row label
    index: int | None = None  # html_table: 0-based column index (post-transpose)
    # html_table: when one cell packs several times (e.g. begin + iqamah), the
    # 0-based position of this column's time within that cell. None → first time.
    time_index: int | None = None
    selector: str | None = None  # html_repeated: CSS selector within a row item
    header_seen: str | None = None  # raw header text captured at authoring
    value_kind: Literal["time", "offset"] | None = None  # None == "time"
    base_prayer: Prayer | None = None  # offset base; None → this column's own prayer


class DateSpec(BaseModel):
    index: int | None = None
    selector: str | None = None
    format: str | None = None  # "day_only"|"dd/mm"|"d_month"|"iso"|None (auto)


class GridSpec(BaseModel):
    table_selector: str | None = None  # html_table: CSS for the <table>
    row_selector: str | None = None  # html_repeated: CSS for each day item
    # Div-grid source: the layout is a <table>-shaped grid built from <div>s
    # (ARIA role="table"/"row"/"cell" or repeated sibling-row containers), not a
    # real <table>. The engine rebuilds the matrix via dom_matrix() each run, so
    # no brittle per-element selector is stored. None/absent → a real <table>.
    dom_grid: bool | None = None
    transpose: bool = False
    # Prayer-rows orientation: a label column names the prayer on each body row,
    # while the header names the kind (Begin/Iqamah). None → prayers are in the
    # header (the default columns layout).
    prayer_label_index: int | None = None
    # Single-day table: no date axis (today's times, re-rendered daily). The engine
    # stamps every extracted cell with the run date. None/absent → multi-day.
    single_day: bool | None = None
    # Month-section layout: an annual page where day-only rows are scoped by a
    # month caption (one table per month, or full-width month rows within one
    # table). The engine reads each section's month from its caption and pairs it
    # with the day number. None/absent → the month comes from the run context.
    month_sections: bool | None = None
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


class WidgetSpec(BaseModel):
    platform: str
    data_url: str | None = None


class BespokeSpec(BaseModel):
    module: str  # registry key for the agent-written extractor module


class MediaSpec(BaseModel):
    # Where an image/PDF timetable lives, recorded so the (deferred) media
    # extraction phase can come back to it. The shape ("image"/"pdf") names the
    # kind; for a per-month image the url points at the currently-visible month.
    url: str


class NavSpec(BaseModel):
    # How a JS calendar exposes the next month. "next" clicks a forward control;
    # "select" picks the target month (and optionally year) from a dropdown.
    kind: Literal["next", "select"] = "next"
    next_selector: str | None = None  # kind="next": CSS for the forward control
    month_select: str | None = None  # kind="select": CSS for the month <select>
    year_select: str | None = None  # kind="select": CSS for the year <select> (optional)
    ready_selector: str | None = None  # element awaited after each step
    settle_ms: int = 800  # fallback wait when no ready_selector

    @model_validator(mode="after")
    def _check_kind(self) -> "NavSpec":
        if self.kind == "next" and not self.next_selector:
            raise ValueError("nav kind 'next' requires next_selector")
        if self.kind == "select" and not self.month_select:
            raise ValueError("nav kind 'select' requires month_select")
        return self


class PagingSpec(BaseModel):
    # url_template: the month lives at a dynamic path; format it per month.
    # render_nav: one URL, drive a headless browser to each month via NavSpec.
    mode: Literal["url_template", "render_nav"]
    url_template: str | None = None  # e.g. "https://x.org/{year}/{month:02d}"
    nav: NavSpec | None = None

    @model_validator(mode="after")
    def _check_mode(self) -> "PagingSpec":
        if self.mode == "url_template" and not self.url_template:
            raise ValueError("paging mode 'url_template' requires url_template")
        if self.mode == "render_nav" and self.nav is None:
            raise ValueError("paging mode 'render_nav' requires nav")
        return self


class SourceConfig(BaseModel):
    shape: Shape
    grid: GridSpec | None = None
    jumuah: JumuahSpec | None = None
    rules: RulesSpec | None = None
    widget: WidgetSpec | None = None
    bespoke: BespokeSpec | None = None
    media: MediaSpec | None = None  # image/pdf: location of the deferred timetable
    paging: PagingSpec | None = None  # opt-in multi-month crawling

    @model_validator(mode="after")
    def _check_shape(self) -> "SourceConfig":
        if self.shape in {"html_table", "html_repeated", "dom_records"} and self.grid is None:
            raise ValueError(f"shape {self.shape!r} requires a grid spec")
        if self.shape == "rules" and self.rules is None:
            raise ValueError("shape 'rules' requires a rules spec")
        if self.shape == "widget" and self.widget is None:
            raise ValueError("shape 'widget' requires a widget spec")
        if self.shape == "bespoke" and self.bespoke is None:
            raise ValueError("shape 'bespoke' requires a bespoke spec")
        if self.shape in {"image", "pdf"} and self.media is None:
            raise ValueError(f"shape {self.shape!r} requires a media spec")
        return self

    @classmethod
    def from_json(cls, raw: str) -> "SourceConfig":
        return cls.model_validate_json(raw)

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


_GRID_SHAPES = frozenset({"html_table", "html_repeated", "dom_records"})


def authoring_problems(config: SourceConfig) -> list[str]:
    """Structural defects in a model-authored grid config that the pydantic schema
    accepts but the engine would silently extract **zero rows** from — a missing
    column index/selector, no date axis, an absent row selector. Returns
    human-readable problems (empty list when sound) so the authoring funnel can feed
    the model an actionable reason ("column 2 needs a 0-based 'index'") instead of a
    bare "no occurrences produced" that it cannot learn from.

    Scoped to grid shapes; widget/rules/media/bespoke carry no column grid. The
    widget *platform* (is there a registered extractor?) is checked separately in the
    funnel, which can see the extractor registry without a circular import here."""
    if config.shape not in _GRID_SHAPES:
        return []
    grid = config.grid
    if grid is None:  # the pydantic validator already guards this; belt and braces.
        return [f"shape {config.shape!r} requires a grid spec"]

    problems: list[str] = []
    uses_selector = config.shape == "html_repeated"

    if uses_selector and not (grid.row_selector and grid.row_selector.strip()):
        problems.append("html_repeated needs a 'row_selector' for each day item")

    if not grid.columns:
        problems.append("grid has no columns to read prayer times from")
    for i, col in enumerate(grid.columns):
        who = f"column {i} ({col.kind} {col.prayer.value if col.prayer else 'label'})"
        if uses_selector:
            if not (col.selector and col.selector.strip()):
                problems.append(f"{who} needs a CSS 'selector' for html_repeated")
        elif col.index is None:
            problems.append(f"{who} needs a 0-based 'index' for {config.shape}")
        elif col.index < 0:
            problems.append(f"{who} has a negative index {col.index}")
        if col.time_index is not None and col.time_index < 0:
            problems.append(f"{who} has a negative time_index {col.time_index}")
    if grid.prayer_label_index is not None and grid.prayer_label_index < 0:
        problems.append("prayer_label_index is negative")

    # Every grid config needs SOME date axis, or it extracts nothing: an explicit
    # date column/selector, a single-day flag, month sections, or paging.
    has_date_axis = (
        grid.date is not None
        or bool(grid.single_day)
        or bool(grid.month_sections)
        or config.paging is not None
    )
    if not has_date_axis:
        problems.append(
            "no date axis: set 'date', or 'single_day' (today-only), or "
            "'month_sections', or 'paging'"
        )
    elif grid.date is not None and not grid.single_day and not grid.month_sections:
        if uses_selector and not (grid.date.selector and grid.date.selector.strip()):
            problems.append("date needs a 'selector' for html_repeated")
        if not uses_selector and grid.date.index is None:
            problems.append(f"date needs a 0-based 'index' for {config.shape}")

    return problems
