"""Turn a raw verification/parse error into a coarse ``FailureKind``, and route the
next authoring action from it.

The single-shot funnel used to answer every rejection with the same generic
"you were rejected, try again" prompt. That wastes a model call on failures a
different *kind* of prompt (or no prompt at all) handles better: a zero-rows
rejection wants a table-mapping repair, not a re-ask; a fetch-empty page wants a
terminal-classification check. ``classify_failure`` + ``feedback_prompt_kind``
make the retry specific to what actually went wrong.
"""

from enum import Enum


class FailureKind(Enum):
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    LINT = "lint"
    ZERO_ROWS = "zero_rows"
    SELF_MATCH = "self_match"
    PLAUSIBILITY = "plausibility"
    INCOMPLETE = "incomplete"
    FETCH_EMPTY = "fetch_empty"
    RENDER_NEEDED = "render_needed"
    UNKNOWN = "unknown"


def classify_failure(error: str | None) -> FailureKind:
    """Map a verify/parse error string to a FailureKind. Checks are ordered most-
    specific first; an unrecognised error is ``UNKNOWN``."""
    e = (error or "").lower()
    if not e:
        return FailureKind.UNKNOWN
    if "no json object" in e or "not a json object" in e:
        return FailureKind.INVALID_JSON
    if "lint:" in e:
        return FailureKind.LINT
    if "no occurrences produced" in e:
        return FailureKind.ZERO_ROWS
    if "self-match failed" in e:
        return FailureKind.SELF_MATCH
    if "out of window" in e or "non-monotonic" in e or "jumuah" in e:
        return FailureKind.PLAUSIBILITY
    if "incomplete" in e or "missing" in e:
        return FailureKind.INCOMPLETE
    if "render failed" in e or "empty body" in e or "navigation produced no" in e:
        return FailureKind.FETCH_EMPTY
    # Pydantic validation / our own "invalid config:" wrapper land here last, so a
    # more specific JSON failure above is not mislabelled as a schema failure.
    if "invalid config" in e or "validation error" in e or "field required" in e:
        return FailureKind.INVALID_SCHEMA
    return FailureKind.UNKNOWN


# Failures that point at a wrong table mapping (selectors/indices/orientation),
# best addressed by a focused table-repair prompt rather than a re-ask.
_TABLE_REPAIR_FAILURES = frozenset(
    {
        FailureKind.ZERO_ROWS,
        FailureKind.SELF_MATCH,
        FailureKind.PLAUSIBILITY,
        FailureKind.LINT,
        FailureKind.INCOMPLETE,
        FailureKind.INVALID_SCHEMA,
    }
)
_TABLE_KINDS = frozenset({"table_repair", "table_choice", "unknown"})


def feedback_prompt_kind(failure: FailureKind, current: str) -> str:
    """The prompt kind to retry with, given how the last attempt failed and what
    kind it was. A table-shaped failure on a table/unknown prompt escalates to a
    table-repair; a fetch-empty page is routed to terminal classification; anything
    else keeps the current kind (INVALID_JSON just re-asks for clean JSON)."""
    if failure in _TABLE_REPAIR_FAILURES and current in _TABLE_KINDS:
        return "table_repair"
    if failure == FailureKind.FETCH_EMPTY:
        return "terminal"
    return current
