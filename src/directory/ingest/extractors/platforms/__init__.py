# Import detector modules for their register() side effects. Order = registry priority.
from directory.ingest.extractors.platforms import (  # noqa: F401
    iframe_widgets,
    mawaqit,
    wp_prayer,
)
from directory.ingest.extractors.platforms.base import register
from directory.ingest.extractors.platforms.dom_grid import DomGridDetector
from directory.ingest.extractors.platforms.dom_records import DomRecordsDetector
from directory.ingest.extractors.platforms.endpoint_month import EndpointMonthDetector
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector
from directory.ingest.extractors.platforms.wp_dpt import WpDptDetector

# Registered last (after the self-registering platform modules above), in priority
# order:
#   1. wp_dpt — a known plugin whose full month grid is reached via a £0 data
#      endpoint (no browser); its dedicated detector wins before the generic ones.
#   2. endpoint_month — the generalised version: derive an unknown plugin's month
#      endpoint, or drive a month <select>, to recover the whole horizon.
#   3. generic_table — the next catch-all for a real <table> on the handed page.
#   4. dom_grid / dom_records — non-<table> fallbacks (real markup wins over a
#      synthesised grid; a structured div-grid is tried before a free record stream).
# Every deterministic tier above resolves a source to "authored" in discovery,
# so the LLM authoring funnel only ever sees what none of them could capture.
register(WpDptDetector())
register(EndpointMonthDetector())
register(GenericTableDetector())
register(DomGridDetector())
register(DomRecordsDetector())
__all__ = ["WpDptDetector", "EndpointMonthDetector"]
