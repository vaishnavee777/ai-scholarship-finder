"""Client logic against a fake MCP session (clearly a mock). The real server is exercised in test_live.py."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from config.settings import Settings
from services.playwright_mcp_client import PageFetchError, PlaywrightMCPClient, parse_tool_result
from services.source_validator import RobotsChecker


def wrap(obj) -> str:
    return f"### Result\n{json.dumps(obj, indent=2)}\n### Ran Playwright code\n```js\nawait page.evaluate()\n```"


class FakeSession:
    def __init__(self, page=None, fail_navigations=0, final_url=None, is_error_text=None):
        self.calls, self.fail, self.page = [], fail_navigations, page or {"title": "T", "text": "hello", "links": []}
        self.final_url, self.err = final_url, is_error_text

    async def call_tool(self, name, args):
        self.calls.append(name)
        if name == "browser_navigate" and self.fail > 0:
            self.fail -= 1
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="Error: page.goto: Timeout 30000ms exceeded")])
        if self.err:
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=self.err)])
        if name == "browser_evaluate":
            body = {"url": self.final_url or "https://scholarships.gov.in/", **self.page}
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=wrap(body))])
        return SimpleNamespace(is_error=False, content=[SimpleNamespace(text="ok")])


def make_client(session, **kw):
    s = Settings(page_timeout_seconds=2, max_retries=kw.pop("retries", 2), respect_robots=False)
    c = PlaywrightMCPClient(s, RobotsChecker(), session=session)
    c.tools = {"browser_navigate", "browser_evaluate"}
    return c


@pytest.fixture(autouse=True)
def no_sleep_or_dns(monkeypatch):
    async def fast(_): return None
    monkeypatch.setattr(asyncio, "sleep", fast)
    import services.playwright_mcp_client as m
    monkeypatch.setattr(m, "validate_public_url", lambda u, resolve=True: __import__("services.source_validator", fromlist=["x"]).validate_public_url(u, resolve=False))


def test_parse_tool_result_handles_wrapper():
    assert parse_tool_result(wrap({"a": 1})) == {"a": 1}
    with pytest.raises(ValueError):
        parse_tool_result("no result here")


def test_fetch_page_parses_text_and_links():
    page = {"title": "NSP", "text": "Scholarships 2026-27", "links": [{"text": "Guidelines", "url": "https://scholarships.gov.in/g"}, {"bad": 1}]}
    pc = asyncio.run(make_client(FakeSession(page)).fetch_page("https://scholarships.gov.in/"))
    assert pc.title == "NSP" and pc.text.startswith("Scholarships") and [l.url for l in pc.links] == ["https://scholarships.gov.in/g"]
    assert pc.retrieved_at.tzinfo is not None


def test_transient_failures_are_retried_then_succeed():
    sess = FakeSession(fail_navigations=2)
    pc = asyncio.run(make_client(sess).fetch_page("https://scholarships.gov.in/"))
    assert pc.text == "hello" and sess.calls.count("browser_navigate") == 3


def test_retry_limit_respected():
    sess = FakeSession(fail_navigations=99)
    with pytest.raises(PageFetchError):
        asyncio.run(make_client(sess).fetch_page("https://scholarships.gov.in/"))
    assert sess.calls.count("browser_navigate") == 3  # 1 try + 2 retries


def test_non_transient_error_not_retried():
    sess = FakeSession(is_error_text="Error: something permanent")
    with pytest.raises(PageFetchError):
        asyncio.run(make_client(sess).fetch_page("https://scholarships.gov.in/"))
    assert sess.calls.count("browser_navigate") == 1


def test_unsafe_url_never_reaches_browser():
    sess = FakeSession()
    with pytest.raises(PageFetchError, match="blocked"):
        asyncio.run(make_client(sess).fetch_page("http://169.254.169.254/latest"))
    assert sess.calls == []


def test_redirect_to_private_host_discards_content():
    sess = FakeSession(final_url="http://10.0.0.1/admin", page={"title": "secret", "text": "internal", "links": []})
    with pytest.raises(PageFetchError, match="redirected"):
        asyncio.run(make_client(sess).fetch_page("https://scholarships.gov.in/"))


def test_robots_block(monkeypatch):
    s = Settings(respect_robots=True)
    class R:
        def allowed(self, u): return False
    c = PlaywrightMCPClient(s, R(), session=FakeSession())
    with pytest.raises(PageFetchError, match="robots"):
        asyncio.run(c.fetch_page("https://scholarships.gov.in/"))
