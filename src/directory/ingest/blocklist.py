from pathlib import Path
from urllib.parse import urlparse

# Hosts that never carry a real mosque timetable: social, video, link
# shorteners, maps, and event booking. Aggregator directories vary by region
# and are best added via the operator override file (Settings.blocklist_path).
_DEFAULT_BLOCKLIST: frozenset[str] = frozenset(
    {
        # social
        "facebook.com",
        "fb.com",
        "fb.me",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "linkedin.com",
        "pinterest.com",
        # video
        "youtube.com",
        "youtu.be",
        # link shorteners / link-in-bio
        "linktr.ee",
        "bit.ly",
        "tinyurl.com",
        # maps / places / shortlinks
        "google.com",
        "goo.gl",
        "g.page",
        "maps.apple.com",
        # messaging
        "whatsapp.com",
        "wa.me",
        "t.me",
        "telegram.me",
        # events / booking
        "eventbrite.com",
        "eventbrite.co.uk",
        # archives / aggregators
        "web.archive.org",
        "mosques.muslimsinbritain.org",
        "heritage.ismaili.net",
        # institutional pages that never carry a mosque jamaat timetable
        "huddersfieldstudent.com",
        "bradfordhospitals.nhs.uk",
    }
)


def _host(url: str) -> str:
    netloc = urlparse(url if "//" in url else f"//{url}").netloc.lower()
    # strip credentials and port
    netloc = netloc.rsplit("@", 1)[-1].split(":", 1)[0]
    return netloc


def load_blocklist(path: Path | None = None) -> frozenset[str]:
    """Default constant merged with an optional newline-delimited override file
    (blank lines and ``#`` comments ignored)."""
    hosts = set(_DEFAULT_BLOCKLIST)
    if path is not None and path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip().lower()
            if line and not line.startswith("#"):
                hosts.add(line)
    return frozenset(hosts)


def is_blocklisted(url: str | None, *, blocklist: frozenset[str] | None = None) -> bool:
    """True when ``url``'s host equals, or is a sub-domain of, a blocklisted host
    (so ``www.facebook.com`` and ``m.facebook.com`` both match ``facebook.com``)."""
    if not url:
        return False
    hosts = blocklist if blocklist is not None else _DEFAULT_BLOCKLIST
    host = _host(url)
    if not host:
        return False
    return any(host == b or host.endswith(f".{b}") for b in hosts)
