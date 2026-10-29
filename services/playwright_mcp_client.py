"""Python MCP client for the Playwright MCP server (stdio transport).

This talks to a real ``@playwright/mcp`` process (launched via ``npx`` by default, see Settings).
It is NOT connected to Claude Code's own MCP configuration; the application owns its server process.
Tools used (verified against the server's tool list at connect time):
  * browser_navigate  - open a URL
  * browser_evaluate  - read visible text, title, final URL and links from the page
  * browser_wait_for  - brief settle time for script-rendered pages
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config.settings import Settings
from services.source_validator import RobotsChecker, UnsafeURLError, validate_public_url

log = logging.getLogger(__name__)

REQUIRED_TOOLS = {"browser_navigate", "browser_evaluate"}

# Runs inside the page. Output is capped here so a hostile page cannot flood the client.
_EXTRACT_JS = """() => ({
  title: document.title || "",
  url: location.href,
  text: (document.body ? document.body.innerText : "").slice(0, %(max_chars)d),
  links: [...document.querySelectorAll('a[href]')].slice(0, 400).map(a => {
    const row = a.closest('tr,li,article,p') || a.parentElement;
    const ctx = row && row.innerText && row.innerText.length < 400 ? row.innerText.trim().replace(/\\s+/g, ' ').slice(0, 200) : '';
    return {text: (a.innerText || a.title || '').trim().slice(0, 120), url: a.href, context: ctx};
  })
})"""


class MCPUnavailableError(RuntimeError):
    """The Playwright MCP server could not be started or lacks required tools."""


class PageFetchError(RuntimeError):
    def __init__(self, url: str, reason: str, transient: bool = False):
        super().__init__(f"{url}: {reason}")
        self.url, self.reason, self.transient = url, reason, transient


@dataclass
class Link:
    text: str
    url: str
    context: str = ""  # nearby row / list-item text; helps rank "Specifications"-style anchors


@dataclass
class PageContent:
    requested_url: str
    final_url: str
    title: str
    text: str
    links: list[Link] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def parse_tool_result(text: str) -> object:
    """Playwright MCP wraps return values as '### Result\\n<json>\\n### Ran Playwright code ...'."""
    m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", text, re.S)
    if not m:
        raise ValueError("no '### Result' section in tool output")
    return json.loads(m.group(1).strip())


def _result_text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


class PlaywrightMCPClient:
    def __init__(self, settings: Settings, robots: RobotsChecker | None = None, session=None):
        self.settings = settings
        self.robots = robots or RobotsChecker()
        self._session = session  # injectable for tests
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()  # one browser, one page at a time (concurrency limit = 1)
        self.tools: set[str] = set()

    async def __aenter__(self) -> "PlaywrightMCPClient":
        if self._session is None:
            await self._connect()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception as e:  # shutting down must never mask the real error
                log.debug("MCP shutdown error: %s", e)

    async def _connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        s = self.settings
        s.mcp_output_dir.mkdir(parents=True, exist_ok=True)
        args = list(s.mcp_args)
        if "--output-dir" not in args:
            args += ["--output-dir", str(s.mcp_output_dir)]
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(
                stdio_client(StdioServerParameters(command=s.mcp_command, args=args)))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(self._session.initialize(), timeout=120)  # first run may download the package
            listing = await self._session.list_tools()
        except Exception as e:
            await self.__aexit__()
            raise MCPUnavailableError(
                f"Could not start the Playwright MCP server ('{s.mcp_command} {' '.join(args)}'): {e}. "
                "Check that Node.js/npx is installed and the network is reachable.") from e
        self.tools = {t.name for t in listing.tools}
        missing = REQUIRED_TOOLS - self.tools
        if missing:
            await self.__aexit__()
            raise MCPUnavailableError(f"Playwright MCP server lacks required tools: {sorted(missing)}")

    async def _call(self, name: str, args: dict, timeout: float) -> str:
        try:
            res = await asyncio.wait_for(self._session.call_tool(name, args), timeout=timeout)
        except asyncio.TimeoutError:
            raise PageFetchError(args.get("url", name), f"{name} timed out after {timeout:.0f}s", transient=False)  # a hung site rarely recovers; do not burn 3x the timeout
        text = _result_text(res)
        if res.is_error:
            transient = any(k in text.lower() for k in ("timeout", "net::err", "navigation", "target closed"))
            raise PageFetchError(args.get("url", name), text.strip().splitlines()[0][:200] if text.strip() else "tool error", transient)
        return text

    async def fetch_page(self, url: str) -> PageContent:
        """Navigate to a public page and return its visible text and links. Retries transient failures."""
        try:
            url = validate_public_url(url)
        except UnsafeURLError as e:
            raise PageFetchError(url, f"blocked by URL safety check ({e})")
        if self.settings.respect_robots and not await asyncio.to_thread(self.robots.allowed, url):
            raise PageFetchError(url, "disallowed by robots.txt (or robots.txt unavailable)")
        async with self._lock:
            last: PageFetchError | None = None
            for attempt in range(self.settings.max_retries + 1):
                try:
                    return await self._fetch_once(url)
                except PageFetchError as e:
                    last = e
                    if not e.transient or attempt == self.settings.max_retries:
                        break
                    await asyncio.sleep(1.5 * (attempt + 1))
            raise last  # type: ignore[misc]

    @staticmethod
    def _looks_unrendered(raw: str) -> bool:
        try:
            return len(str(parse_tool_result(raw).get("text", ""))) < 500
        except Exception:
            return True

    async def _fetch_once(self, url: str) -> PageContent:
        t = self.settings.page_timeout_seconds
        await self._call("browser_navigate", {"url": url}, t)
        js = _EXTRACT_JS % {"max_chars": self.settings.max_page_chars}
        raw = await self._call("browser_evaluate", {"function": js}, t)
        if "browser_wait_for" in self.tools and self._looks_unrendered(raw):
            # Script-rendered page: give it a moment to settle, then read again (best-effort).
            try:
                await self._call("browser_wait_for", {"time": 1.5}, 10)
                raw = await self._call("browser_evaluate", {"function": js}, t)
            except PageFetchError:
                pass
        try:
            data = parse_tool_result(raw)
            final_url = str(data["url"])
        except (ValueError, KeyError, TypeError) as e:
            raise PageFetchError(url, f"unreadable page result ({e})", transient=False)
        # Redirects may have led somewhere unsafe: re-validate and discard the content if so.
        try:
            final_url = validate_public_url(final_url)
        except UnsafeURLError as e:
            raise PageFetchError(url, f"redirected to a blocked destination ({e})")
        links = []
        for l in data.get("links", []):
            if isinstance(l, dict) and isinstance(l.get("url"), str):
                links.append(Link(text=str(l.get("text", "")), url=l["url"], context=str(l.get("context", ""))))
        return PageContent(
            requested_url=url, final_url=final_url, title=str(data.get("title", ""))[:300],
            text=str(data.get("text", ""))[: self.settings.max_page_chars], links=links,
        )
