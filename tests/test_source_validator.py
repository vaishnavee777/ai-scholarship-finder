import pytest

from models.scholarship import SourceReliability as R
from services.source_validator import (
    RobotsChecker, UnsafeURLError, classify_source, is_official_domain, same_site, validate_public_url,
)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "ftp://scholarships.gov.in/x", "http://localhost/", "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/",
    "http://user:pass@scholarships.gov.in/", "https://scholarships.gov.in:8443/", "http://intranet/", "http://foo.internal/",
    "https://example.com/a b", "http:///nohost", "http://0.0.0.0/", "http://2130706433/",
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        validate_public_url(url, resolve=False)


def test_public_url_accepted_and_fragment_stripped():
    assert validate_public_url("https://scholarships.gov.in/home#top", resolve=False) == "https://scholarships.gov.in/home"
    assert validate_public_url("http://8.8.8.8/", resolve=False)


def test_hostname_resolving_to_private_ip_rejected(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://sneaky.example.org/")


def test_unresolvable_host_rejected(monkeypatch):
    import socket
    def boom(*a, **k): raise socket.gaierror
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://nope.example.org/")


@pytest.mark.parametrize("url,expected", [
    ("https://scholarships.gov.in/home", R.OFFICIAL_GOVERNMENT),
    ("https://services.india.gov.in/", R.OFFICIAL_GOVERNMENT),
    ("https://nsp.nic.in/x", R.OFFICIAL_GOVERNMENT),
    ("https://www.iitm.ac.in/scholarships", R.OFFICIAL_INSTITUTION),
    ("https://www.buddy4study.com/page/x", R.THIRD_PARTY),
    ("https://evilgov.in/", R.UNVERIFIED),
    ("https://scholarships.gov.in.evil.com/", R.UNVERIFIED),
    ("https://random-blog.com/scholarship", R.UNVERIFIED),
])
def test_classification(url, expected):
    assert classify_source(url) == expected


def test_trusted_provider_allowlist():
    assert classify_source("https://www.tatatrusts.org/x") == R.UNVERIFIED
    assert classify_source("https://www.tatatrusts.org/x", ("tatatrusts.org",)) == R.OFFICIAL_PROVIDER
    assert not is_official_domain("https://evilgov.in/")


def test_same_site():
    assert same_site("https://scholarships.gov.in/a", "https://nsp.gov.in/b") is False  # different registrable names
    assert same_site("https://www.iitm.ac.in/a", "https://research.iitm.ac.in/b")
    assert not same_site("https://iitm.ac.in/a", "https://evil.ac.in/b")


class _Resp:
    def __init__(self, code, text=""): self.status_code, self.text = code, text


def test_robots(monkeypatch):
    import httpx
    rules = "User-agent: *\nDisallow: /private/\n"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, rules))
    rc = RobotsChecker()
    assert rc.allowed("https://a.gov.in/public") and not rc.allowed("https://a.gov.in/private/x")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(404))
    assert RobotsChecker().allowed("https://b.gov.in/private/x")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(503))
    assert not RobotsChecker().allowed("https://c.gov.in/x")
    def boom(*a, **k): raise httpx.ConnectError("x")
    monkeypatch.setattr(httpx, "get", boom)
    assert not RobotsChecker().allowed("https://d.gov.in/x")
