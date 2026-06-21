# Import detector modules for their register() side effects. Order = registry priority.
from directory.ingest.extractors.platforms import (  # noqa: F401
    iframe_widgets,
    mawaqit,
    wp_prayer,
)
from directory.ingest.extractors.platforms.base import register
from directory.ingest.extractors.platforms.generic_table import GenericTableDetector

# Registered last (after the self-registering platform modules above): platform-
# specific detectors win; the generic table detector is the catch-all.
register(GenericTableDetector())
