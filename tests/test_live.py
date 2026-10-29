"""Live tests: need Node/npx, network access, and RUN_LIVE=1. They hit real public websites."""
import asyncio
import os

import pytest

from config.settings import get_settings
from services.playwright_mcp_client import PlaywrightMCPClient

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to run live browser tests")


def test_real_mcp_fetches_public_page_and_blocks_private():
    async def go():
        async with PlaywrightMCPClient(get_settings()) as c:
            assert {"browser_navigate", "browser_evaluate"} <= c.tools
            page = await c.fetch_page("https://scholarships.gov.in/")
            assert "scholarship" in page.text.lower() and page.links
            assert page.final_url.startswith("https://")
            with pytest.raises(Exception, match="blocked"):
                await c.fetch_page("http://127.0.0.1:8501/")
    asyncio.run(go())
