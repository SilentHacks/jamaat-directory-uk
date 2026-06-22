# Import detector modules for their register() side effects. Order = registry priority.
from directory.ingest.extractors.platforms import (  # noqa: F401
    iframe_widgets,
    mawaqit,
    wp_prayer,
)
from directory.ingest.extractors.platforms.base import register
from directory.ingest.extractors.platforms.dom_records import DomRecordsDetector
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector

# Registered last (after the self-registering platform modules above): platform-
# specific detectors win; the generic <table> detector is the next catch-all, and
# the non-<table> detectors are the final fallback (real markup always wins over a
# synthesised grid / content stream).
register(GenericTableDetector())
register(DomRecordsDetector())
