# Import detector modules for their register() side effects. Order = registry priority.
from directory.ingest.extractors.platforms import (  # noqa: F401
    iframe_widgets,
    mawaqit,
    wp_prayer,
)
from directory.ingest.extractors.platforms.base import register
from directory.ingest.extractors.platforms.dom_grid import DomGridDetector
from directory.ingest.extractors.platforms.dom_records import DomRecordsDetector
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector

# Registered last (after the self-registering platform modules above): platform-
# specific detectors win; the generic <table> detector is the next catch-all, then
# the non-<table> fallbacks (real markup always wins over a synthesised grid).
# Among the fallbacks a structured div-grid is tried before a free record stream.
register(GenericTableDetector())
register(DomGridDetector())
register(DomRecordsDetector())
