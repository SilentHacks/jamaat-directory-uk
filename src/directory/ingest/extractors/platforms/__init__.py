# Import detector modules for their register() side effects. Order = registry priority.
from directory.ingest.extractors.platforms import (  # noqa: F401,E402
    iframe_widgets,
    mawaqit,
    wp_prayer,
)
