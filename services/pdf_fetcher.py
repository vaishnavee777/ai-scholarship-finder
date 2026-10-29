"""Safe retrieval of guideline PDFs over plain HTTP.

The browser returns no text for PDFs, and official eligibility rules are usually published as PDFs.
Every hop is validated (SSRF), robots.txt is honoured, size and time are capped, and redirects are
followed manually so each destination is re-checked.
"""
from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from services.playwright_mcp_client import PageFetchError
from services.source_validator import USER_AGENT, RobotsChecker, UnsafeURLError, validate_public_url

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 25
MAX_REDIRECTS = 4


def is_pdf_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


def extract_pdf_text(data: bytes, max_chars: int, max_pages: int = MAX_PDF_PAGES) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts, total = [], 0
    for page in reader.pages[:max_pages]:
        t = (page.extract_text() or "").strip()
        parts.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return "\n\n".join(parts)[:max_chars]


def _download(url: str, timeout: float) -> tuple[str, bytes]:
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            url = validate_public_url(url)
            with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise PageFetchError(url, f"HTTP {r.status_code}", transient=r.status_code >= 500)
                ctype = r.headers.get("content-type", "").lower()
                if "pdf" not in ctype and not is_pdf_url(url):
                    raise PageFetchError(url, f"not a PDF ({ctype or 'unknown type'})")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_PDF_BYTES:
                        raise PageFetchError(url, "PDF larger than the size limit")
                return url, bytes(buf)
    raise PageFetchError(url, "too many redirects")


async def fetch_pdf(url: str, robots: RobotsChecker, timeout: float, max_chars: int, respect_robots: bool = True):
    """Returns (final_url, text, retrieved_at). Raises PageFetchError."""
    try:
        url = validate_public_url(url)
    except UnsafeURLError as e:
        raise PageFetchError(url, f"blocked by URL safety check ({e})")
    if respect_robots and not await asyncio.to_thread(robots.allowed, url):
        raise PageFetchError(url, "disallowed by robots.txt (or robots.txt unavailable)")
    try:
        final_url, data = await asyncio.to_thread(_download, url, timeout)
    except UnsafeURLError as e:
        raise PageFetchError(url, f"redirected to a blocked destination ({e})")
    except httpx.HTTPError as e:
        raise PageFetchError(url, f"network error: {type(e).__name__}", transient=True)
    try:
        text = await asyncio.to_thread(extract_pdf_text, data, max_chars)
    except Exception as e:
        raise PageFetchError(url, f"could not read PDF ({type(e).__name__})")
    if len(text.strip()) < 40:
        raise PageFetchError(url, "PDF has no extractable text (possibly scanned images)")
    return final_url, text, datetime.now(timezone.utc)
