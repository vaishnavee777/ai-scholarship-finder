"""Thin harness: drives the real @playwright/mcp server over stdio and logs every MCP tool call."""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPBrowser:
    def __init__(self, shot_dir: str):
        self.shot_dir = shot_dir
        self.calls: list[dict] = []   # every MCP tool call: name, args, seconds, ok
        self.tools: list[str] = []
        self._stack = AsyncExitStack()
        self.s: ClientSession | None = None

    async def start(self):
        params = StdioServerParameters(command="npx", args=["-y", "@playwright/mcp@0.0.83", "--headless", "--isolated",
                                                            "--output-dir", self.shot_dir])
        r, w = await self._stack.enter_async_context(stdio_client(params))
        self.s = await self._stack.enter_async_context(ClientSession(r, w))
        await self.s.initialize()
        self.tools = [t.name for t in (await self.s.list_tools()).tools]

    async def stop(self):
        await self._stack.aclose()

    async def call(self, name: str, **args) -> str:
        t0 = time.time()
        res = await asyncio.wait_for(self.s.call_tool(name, args), timeout=40)
        text = "\n".join(getattr(c, "text", "") for c in res.content)
        if text.startswith("### Error") and name in ("browser_click", "browser_type"):
            raise RuntimeError(text.splitlines()[1][:160] if len(text.splitlines()) > 1 else "tool error")
        self.calls.append({"tool": name, "args": {k: (v if len(str(v)) < 80 else str(v)[:77] + "...") for k, v in args.items()},
                           "seconds": round(time.time() - t0, 2), "ok": not res.is_error})
        return text

    # -- helpers built only from MCP tools --
    @staticmethod
    def label(text: str) -> str:
        """Pattern for the visible label of a Streamlit radio/checkbox/button (the real input is covered by it)."""
        return rf"paragraph \[ref=\w+\]: .*{re.escape(text)}"

    async def goto(self, url):
        await self.call("browser_navigate", url=url)
        await self.call("browser_wait_for", text="Data source")
        await self.call("browser_wait_for", time=1.5)

    async def snap(self) -> str:
        return await self.call("browser_snapshot")

    async def ref(self, pattern: str, snap: str | None = None, nth: int = 0) -> str:
        snap = snap or await self.snap()
        hits = [m for l in snap.splitlines() if re.search(pattern, l) and (m := re.search(r"\[ref=(\w+)\]", l))]
        if len(hits) <= nth:
            raise LookupError(f"no element matching /{pattern}/ (found {len(hits)})")
        return hits[nth].group(1)

    async def click(self, pattern: str, nth: int = 0, settle: float = 1.5):
        ref = await self.ref(pattern, nth=nth)
        await self.call("browser_click", element=pattern, target=ref)
        if settle:
            await self.call("browser_wait_for", time=settle)

    async def type(self, pattern: str, text: str, nth: int = 0):
        ref = await self.ref(pattern, nth=nth)
        await self.call("browser_type", element=pattern, target=ref, text=text, submit=False)
        await self.call("browser_press_key", key="Tab")
        await self.call("browser_wait_for", time=1.5)

    async def choose(self, combobox_label: str, option: str):
        await self.click(rf'combobox "{combobox_label}"', settle=0.8)
        await self.call("browser_press_key", key=option)  # type-ahead in the open list
        await self.call("browser_press_key", key="Enter")
        await self.call("browser_wait_for", time=1.5)

    async def shot(self, name: str) -> str:
        fn = f"{self.shot_dir}/{name}.png"
        await self.call("browser_take_screenshot", type="png", filename=fn, fullPage=False)
        return f"{name}.png"

    async def js(self, expr: str):
        """Run a JS function in the page via browser_evaluate and return the parsed JSON result."""
        import json
        raw = await self.call("browser_evaluate", function=expr)
        m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", raw, re.S)
        return json.loads(m.group(1).strip()) if m else None

    async def text(self) -> str:
        return await self.js("() => document.body.innerText") or ""
