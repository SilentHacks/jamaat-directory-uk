"""The four table orientations and the single GridSpec construction shared by
every path that builds one.

A timetable table maps to a ``GridSpec`` four ways, and the orientation→flags
knowledge (``transpose`` / ``single_day`` / ``prayer_label_index`` / ``date``)
used to be re-encoded in three places: the ``generic_table`` detectors, the
config enumerator (via those detectors), and ``author.config_from_table_mapping``
(building a grid from a model's compact ``table_mapping``). ``grid_for`` is the
one place that mapping lives, and the orientation names below are the one
vocabulary the prompt schema, the decision parser, and the detectors all share.
"""

from directory.ingest.extractors.config_schema import ColumnSpec, DateSpec, GridSpec

# Orientation names — the model ``table_mapping`` vocabulary, and (prefixed with
# ``table_``) the enumerator's provenance labels.
HORIZONTAL_MULTIDAY = "horizontal_multiday"
TRANSPOSE_MULTIDAY = "transpose_multiday"
HORIZONTAL_SINGLE_DAY = "horizontal_single_day"
PRAYER_ROWS = "prayer_rows"  # prayers down a label column (a.k.a. vertical single-day)

ORIENTATIONS = frozenset(
    {HORIZONTAL_MULTIDAY, TRANSPOSE_MULTIDAY, HORIZONTAL_SINGLE_DAY, PRAYER_ROWS}
)


def grid_for(
    orientation: str,
    *,
    columns: list[ColumnSpec],
    selector: str | None = None,
    date_index: int | None = None,
    label_index: int | None = None,
) -> GridSpec:
    """The ``GridSpec`` for one orientation. The single source of the
    orientation→flags mapping, shared by the deterministic detectors and the
    model ``table_mapping`` builder so they cannot drift. Unknown orientations
    fall back to ``horizontal_multiday`` (the default columns layout)."""
    if orientation == TRANSPOSE_MULTIDAY:
        return GridSpec(
            table_selector=selector, transpose=True,
            date=DateSpec(index=date_index), columns=columns,
        )
    if orientation == HORIZONTAL_SINGLE_DAY:
        return GridSpec(table_selector=selector, single_day=True, columns=columns)
    if orientation == PRAYER_ROWS:
        return GridSpec(
            table_selector=selector, prayer_label_index=label_index,
            single_day=True, columns=columns,
        )
    return GridSpec(
        table_selector=selector, date=DateSpec(index=date_index), columns=columns
    )
