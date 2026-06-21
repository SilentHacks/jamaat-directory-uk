from directory.ingest.blocklist import is_blocklisted, load_blocklist


def test_exact_host_blocked():
    assert is_blocklisted("https://facebook.com/masjid")


def test_www_and_subdomain_blocked():
    assert is_blocklisted("https://www.facebook.com/x")
    assert is_blocklisted("https://m.facebook.com/x")
    assert is_blocklisted("https://maps.google.com/?q=masjid")


def test_real_mosque_host_allowed():
    assert not is_blocklisted("https://leicestermosque.org/prayer-times")
    assert not is_blocklisted("https://notfacebook.com/")  # suffix must be on a dot boundary


def test_none_and_empty_are_not_blocked():
    assert not is_blocklisted(None)
    assert not is_blocklisted("")
    assert not is_blocklisted("not a url")


def test_port_and_credentials_stripped():
    assert is_blocklisted("https://user:pw@www.facebook.com:8443/x")


def test_override_file_merges(tmp_path):
    f = tmp_path / "extra.txt"
    f.write_text("# operator additions\naggregator.example\n\n", encoding="utf-8")
    bl = load_blocklist(f)
    assert is_blocklisted("https://www.aggregator.example/list", blocklist=bl)
    assert is_blocklisted("https://facebook.com/x", blocklist=bl)  # defaults still present
    # default-only blocklist does not include the override host
    assert not is_blocklisted("https://aggregator.example/list")
