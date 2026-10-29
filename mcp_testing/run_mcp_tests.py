"""MCP-driven UI tests: Claude-style tool calls (Playwright MCP) drive the running Streamlit app.

Usage:  streamlit run app.py   (port 8501)   then   python mcp_testing/run_mcp_tests.py [--live]
Writes mcp_testing/results.json and screenshots/*.png
"""
import asyncio, json, re, sys, time, traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_harness import MCPBrowser

HERE = Path(__file__).parent
URL = "http://localhost:8501"
RESULTS: list[dict] = []


async def fresh(b, demo=True):
    await b.call("browser_resize", width=1280, height=900)
    await b.goto(URL)
    if demo:
        await b.click(b.label("Demo data"))


async def submit(b, settle=4):
    await b.click(b.label("Find scholarships"), settle=settle)


async def case(b, tid, title, steps, expected, fn):
    n0 = len(b.calls)
    t0 = time.time()
    try:
        actual, ok, shot = await fn()
    except Exception as e:
        actual, ok, shot = f"Exception: {e!r}", False, None
        try:
            shot = await b.shot(f"{tid}-error")
        except Exception:
            pass
    RESULTS.append(dict(id=tid, title=title, steps=steps, expected=expected, actual=actual,
                        status="PASS" if ok else "FAIL", screenshot=shot, mcp_calls=len(b.calls) - n0,
                        seconds=round(time.time() - t0, 1)))
    print(f"{tid} {'PASS' if ok else 'FAIL'}  {title}  -> {actual[:110]}", flush=True)


async def main(live: bool):
    b = MCPBrowser(str(HERE / "screenshots"))
    await b.start()
    meta = dict(started=datetime.now().isoformat(timespec="seconds"), server="@playwright/mcp@0.0.83", tools=b.tools, url=URL)

    async def t01():
        await fresh(b, demo=False)
        txt = await b.text()
        title = await b.js("() => document.title")
        errs = await b.call("browser_console_messages", level="error")
        no_err = bool(re.search(r"Errors: 0\b", errs)) or "Total messages: 0" in errs
        ok = title == "AI Scholarship Finder" and "Find scholarships" in txt and no_err
        return f"title={title!r}; heading and Find button present; browser console errors: {'0' if no_err else errs[:80]}", ok, await b.shot("TC01-home")

    async def t02():
        await fresh(b, demo=False)
        txt = await b.text()
        ok = "Fill in your profile (everything is optional)" in txt
        return "Info prompt shown before any search" if ok else "prompt missing", ok, None

    async def t03():
        await fresh(b)
        await submit(b)
        txt = await b.text()
        n = len(re.findall(r"DEMO RECORD", txt))
        ok = "Demo data." in txt and n >= 8 and "Showing 8 of 8" in txt
        return f"Demo banner shown; {n} cards labelled DEMO RECORD; 'Showing 8 of 8' present={('Showing 8 of 8' in txt)}", ok, await b.shot("TC03-demo-results")

    async def t04():
        await fresh(b)
        await b.choose("State of residence / domicile", "Karnataka")
        await b.choose("Category", "OBC")
        await submit(b)
        txt = await b.text()
        m = re.search(r"Showing (\d+) of (\d+)", txt)
        ok = bool(m) and "Traceback" not in txt and "Likely ineligible" in txt
        return f"Results rendered for Karnataka/OBC: {m.group(0) if m else 'none'}; eligibility metrics visible", ok, await b.shot("TC04-karnataka-obc")

    async def t05():
        await fresh(b)
        await submit(b)
        await b.type(r'textbox "Provider / name contains"', "Karnataka")
        txt = await b.text()
        m = re.search(r"Showing (\d+) of (\d+)", txt)
        ok = bool(m) and int(m.group(1)) < int(m.group(2)) and int(m.group(1)) >= 1
        return f"Text filter 'Karnataka' -> {m.group(0) if m else 'no count'}", ok, await b.shot("TC05-text-filter")

    async def t06():
        await fresh(b)
        await submit(b)
        await b.type(r'textbox "Provider / name contains"', "zzzz-no-such-scholarship")
        txt = await b.text()
        ok = "No results match these filters" in txt and "Traceback" not in txt
        return "Warning 'No results match these filters' shown, no crash" if ok else "no empty-filter message", ok, await b.shot("TC06-no-match")

    async def t07():
        await fresh(b)
        await submit(b)
        txt = await b.text()
        ok = "Some details were left blank" in txt
        return "Warning about blank profile fields (potential matches only) displayed" if ok else "warning missing", ok, None

    async def t08():
        await fresh(b)
        await b.click(b.label("Exact amount"))
        txt = await b.text()
        snap = await b.snap()
        ok = "Amount (₹ per year)" in txt
        return "Switching to 'Exact amount' shows the exact-amount number input" if ok else "widget did not change", ok, await b.shot("TC08-income-exact")

    async def t09():
        await fresh(b)
        payload = "<img src=x onerror=alert(1)><script>alert(2)</script>"
        await b.type(r'textbox "Degree / course"', payload)
        await submit(b)
        txt = await b.text()
        injected = await b.js("() => document.querySelectorAll('main img[src=\"x\"], main script:not([src])').length")
        ok = "Traceback" not in txt and injected == 0
        return f"Hostile HTML in course field: injected elements in DOM = {injected}; no dialog/crash", ok, await b.shot("TC09-xss")

    async def t10():
        await fresh(b)
        await b.type(r'spinbutton "Current year"', "99")
        await submit(b)
        txt = await b.text()
        ok = "Traceback" not in txt and "Exception" not in txt
        return "Out-of-range year (99) did not crash the app (Streamlit enforces max 8)", ok, await b.shot("TC10-invalid-year")

    async def t11():
        await fresh(b, demo=False)
        txt = await b.text()
        ok = "Gemini:" in txt
        m = re.search(r"Gemini:[^\n]*", txt)
        return f"Sidebar status: {m.group(0) if m else 'missing'}", ok, None

    async def t12():
        await fresh(b)
        await b.click(b.label("Clear saved searches & AI cache"), settle=2)
        txt = await b.text()
        ok = "Cleared." in txt
        return "Confirmation 'Cleared.' shown" if ok else "no confirmation", ok, await b.shot("TC12-clear")

    async def t13():
        await fresh(b)
        await submit(b)
        await b.call("browser_resize", width=390, height=844)
        await b.call("browser_wait_for", time=2)
        w = await b.js("() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})")
        ok = w["sw"] <= w["iw"] + 4
        return f"Mobile 390px: scrollWidth={w['sw']}, viewport={w['iw']} -> {'no horizontal scroll' if ok else 'overflow'}", ok, await b.shot("TC13-mobile")

    async def t14():
        await fresh(b)
        await b.click(b.label("Force fresh verification"), settle=1)
        await b.click(b.label("Save my search summary on this device"), settle=1.5)
        txt = await b.text()
        ok = "Search history" in txt
        return "Opt-in history checkbox reveals the 'Search history' panel" if ok else "panel missing", ok, await b.shot("TC14-history")

    async def t15():
        await fresh(b)
        await submit(b)
        txt = await b.text()
        has = "DEMO RECORD" in txt and bool(re.search(r"potential match|eligible|unverified", txt, re.I))
        return "Cards show eligibility status and DEMO label" if has else "labels missing", has, None

    cases = [
        ("TC-01", "Home page loads", "Open http://localhost:8501; read title, text, console", "Title 'AI Scholarship Finder', heading + Find button visible, 0 console errors", t01),
        ("TC-02", "Initial empty state", "Open page without searching", "Prompt 'Fill in your profile…' is shown", t02),
        ("TC-03", "Demo search, empty profile", "Select Demo data; click Find scholarships", "8 fictional cards, demo banner, 'Showing 8 of 8'", t03),
        ("TC-04", "Profile: Karnataka + OBC", "Demo data; pick State=Karnataka, Category=OBC; search", "Results and eligibility metrics render without errors", t04),
        ("TC-05", "Text filter narrows list", "Search demo; type 'Karnataka' in name filter", "Count drops below 8 but stays >=1", t05),
        ("TC-06", "Filter with no match", "Type nonsense in name filter", "Friendly 'No results match' warning", t06),
        ("TC-07", "Missing-field warning", "Search with a blank profile", "Warning lists blank details / potential matches", t07),
        ("TC-08", "Income input mode switch", "Click radio 'Exact amount'", "Exact-amount number box appears", t08),
        ("TC-09", "HTML/script injection in text field", "Enter <img onerror><script> in course; search", "Text escaped; nothing injected; no crash", t09),
        ("TC-10", "Out-of-range number", "Type 99 in Current year; search", "App does not crash", t10),
        ("TC-11", "Gemini status indicator", "Read sidebar", "Shows configured / not-configured status", t11),
        ("TC-12", "Clear saved searches button", "Click 'Clear saved searches & AI cache'", "'Cleared.' confirmation", t12),
        ("TC-13", "Mobile layout (390x844)", "Resize to phone size after search", "No horizontal scrolling", t13),
        ("TC-14", "Opt-in search history", "Tick 'Save my search summary'", "Search history panel appears", t14),
        ("TC-15", "Result cards show labels", "Search demo; read cards", "Eligibility status + DEMO label on each card", t15),
    ]
    for tid, title, steps, exp, fn in cases:
        await case(b, tid, title, steps, exp, fn)

    if live:
        async def t16():
            await fresh(b, demo=False)
            await b.choose("State of residence / domicile", "Karnataka")
            await b.choose("Education level", "Undergraduate")
            await b.type(r'textbox "Degree / course"', "B.E.")
            await submit(b, settle=2)
            end = time.time() + 240
            txt = ""
            while time.time() < end:
                await b.call("browser_wait_for", time=10)
                txt = await b.text()
                if re.search(r"Showing \d+ of|No scholarships to show|problem\(s\) during", txt):
                    break
            m = re.search(r"Showing (\d+) of (\d+)", txt)
            prob = re.search(r"(\d+) problem\(s\)", txt)
            ok = bool(m) and "Traceback" not in txt
            return (f"Live search finished: {m.group(0) if m else 'no results listed'}; "
                    f"{prob.group(0) if prob else 'no reported problems'}"), ok, await b.shot("TC16-live")
        await case(b, "TC-16", "Live search against official sites", "Live search; Karnataka, Undergraduate, B.E.; wait for completion",
                   "Real scholarships returned (or problems clearly reported), no crash", t16)

    errs = await b.call("browser_console_messages", level="error", all=True)
    meta["console_errors_summary"] = errs.strip()[:600]
    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    meta["mcp_calls"] = b.calls
    await b.stop()
    json.dump(dict(meta=meta, results=RESULTS), open(HERE / "results.json", "w"), indent=2, ensure_ascii=False)
    p = sum(r["status"] == "PASS" for r in RESULTS)
    print(f"\n{p}/{len(RESULTS)} passed; {len(b.calls)} MCP tool calls")


if __name__ == "__main__":
    asyncio.run(main("--live" in sys.argv))
