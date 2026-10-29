"""URL safety (SSRF) and source-reliability classification.

Only public http(s) destinations are allowed. Hostnames are resolved and every address must be
globally routable. DNS can change between this check and the browser's own lookup, so the browser
client re-validates the *final* URL after navigation and discards content from anything unsafe.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from urllib.parse import urldefrag, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from models.scholarship import SourceReliability

ALLOWED_PORTS = {None, 80, 443}
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".invalid", ".test", ".example")
GOV_SUFFIXES = ("gov.in", "nic.in", "gov")
INSTITUTION_SUFFIXES = ("ac.in", "edu.in", "edu")
# Aggregators and listing sites: useful for discovery, never authoritative.
THIRD_PARTY_DOMAINS = (
    "buddy4study.com", "scholarshipsindia.com", "scholarships.net.in", "indiascholarships.com",
    "vidyasaarathi.co.in", "scholarshipportal.com", "shiksha.com", "collegedunia.com", "careers360.com",
)
USER_AGENT = "AIScholarshipFinder/0.1 (+student research; respects robots.txt)"


class UnsafeURLError(ValueError):
    pass


def normalize_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def validate_public_url(url: str, resolve: bool = True) -> str:
    """Returns the normalized URL or raises UnsafeURLError."""
    if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in "\r\n\t "):
        raise UnsafeURLError("malformed URL")
    parsed = urlparse(normalize_url(url))
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"scheme '{parsed.scheme}' is not allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("credentials in URL are not allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("missing host")
    try:
        if parsed.port not in ALLOWED_PORTS:
            raise UnsafeURLError(f"port {parsed.port} is not allowed")
    except ValueError:
        raise UnsafeURLError("invalid port")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        raise UnsafeURLError("non-public hostname")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise UnsafeURLError("non-public IP address")
        return normalize_url(url)
    if resolve:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise UnsafeURLError("host does not resolve")
        for info in infos:
            if not ipaddress.ip_address(info[4][0]).is_global:
                raise UnsafeURLError("host resolves to a non-public address")
    return normalize_url(url)


def is_safe(url: str, resolve: bool = True) -> bool:
    try:
        validate_public_url(url, resolve)
        return True
    except UnsafeURLError:
        return False


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def is_official_domain(url: str) -> bool:
    h = host_of(url)
    return any(_host_matches(h, s) for s in GOV_SUFFIXES + INSTITUTION_SUFFIXES)


def classify_source(url: str, trusted_domains: tuple[str, ...] = ()) -> SourceReliability:
    h = host_of(url)
    if any(_host_matches(h, d) for d in THIRD_PARTY_DOMAINS):
        return SourceReliability.THIRD_PARTY
    if any(_host_matches(h, s) for s in GOV_SUFFIXES):
        return SourceReliability.OFFICIAL_GOVERNMENT
    if any(_host_matches(h, s) for s in INSTITUTION_SUFFIXES):
        return SourceReliability.OFFICIAL_INSTITUTION
    if any(_host_matches(h, d) for d in trusted_domains):
        return SourceReliability.OFFICIAL_PROVIDER
    return SourceReliability.UNVERIFIED


def same_site(a: str, b: str) -> bool:
    """True when two URLs share a registrable-ish domain (last two labels, three for .gov.in-style)."""
    def root(u: str) -> str:
        parts = host_of(u).split(".")
        n = 3 if len(parts) >= 3 and parts[-2] in ("gov", "nic", "ac", "edu", "co", "org") else 2
        return ".".join(parts[-n:])
    return root(a) == root(b)


class RobotsChecker:
    """robots.txt compliance. Follows common crawler practice:
    4xx -> no restrictions; 5xx / network failure -> treat as disallowed (try later)."""

    def __init__(self, timeout: float = 8.0):
        self._cache: dict[str, RobotFileParser | bool] = {}
        self._lock = threading.Lock()
        self._timeout = timeout

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        with self._lock:
            entry = self._cache.get(origin)
        if entry is None:
            entry = self._load(origin)
            with self._lock:
                self._cache[origin] = entry
        if isinstance(entry, bool):
            return entry
        return entry.can_fetch(USER_AGENT, url)

    def _load(self, origin: str) -> RobotFileParser | bool:
        try:
            r = httpx.get(origin + "/robots.txt", timeout=self._timeout, follow_redirects=False,
                          headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError:
            return False
        if 300 <= r.status_code < 400:
            return True  # unusual; do not follow redirects to unvalidated hosts, assume no restrictions
        if 400 <= r.status_code < 500:
            return True
        if r.status_code >= 500:
            return False
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp
