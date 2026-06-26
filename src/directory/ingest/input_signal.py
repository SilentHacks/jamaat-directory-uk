"""Measure whether the input we hand the authoring model even contains a timetable.

A model "failure" to author is ambiguous: did it misread a timetable it could see,
or did we hand it a page with no times at all (a JS shell, a third-party widget on
another host, the wrong page)? This module scores the model's *actual* input — the
windowed regions and structured table evidence it is shown — so an eval can split
"blinded" from "misread". On the byteplus eval, most needs_reauthor sites were
blinded: zero prayer times reached the model. Tracking this is the difference
between "buy a bigger model" and "fix the plumbing".
"""

from __future__ import annotations

from dataclasses import dataclass

from directory.ingest.discover import CandidateBundle
from directory.ingest.evidence import _TIME_SCAN_RE, _distinct_prayers
from directory.ingest.prompt import _window_region


@dataclass(frozen=True)
class InputSignal:
    """What the model can see for one mosque's bundle."""

    time_count: int  # clock times in the windowed regions the model is shown
    distinct_prayers: int  # distinct daily prayers named across that input
    table_time_count: int  # clock times in the structured table evidence
    has_widget: bool  # a recognised prayer widget hint is present
    has_media: bool  # an image/PDF timetable link is present

    @property
    def has_times(self) -> bool:
        """True when the model's input carries a real timetable signal — clock times
        (or a widget/media timetable it can author without seeing raw times). False
        means we blinded it: no timetable reached the model."""
        return self.time_count > 0 or self.table_time_count > 0 or self.has_widget or self.has_media


def model_input_signal(
    bundle: CandidateBundle, *, max_region_chars: int = 6000, max_candidates: int = 5
) -> InputSignal:
    """Score the input the authoring model receives for ``bundle``. The regions are
    windowed with the SAME budget the prompt uses, so the count reflects exactly what
    the model is shown — not the full fetched page."""
    regions = [
        _window_region(c.region_html, max_region_chars)
        for c in bundle.candidates[:max_candidates]
    ]
    blob = "\n".join(regions)
    time_count = len(_TIME_SCAN_RE.findall(blob))
    distinct = _distinct_prayers(blob)

    table_time_count = sum(t.time_count for e in bundle.evidence for t in e.tables)
    has_widget = any(e.widget_hints for e in bundle.evidence)
    has_media = any(
        m.kind in ("image", "pdf") for e in bundle.evidence for m in e.media_links
    )
    return InputSignal(
        time_count=time_count,
        distinct_prayers=distinct,
        table_time_count=table_time_count,
        has_widget=has_widget,
        has_media=has_media,
    )
