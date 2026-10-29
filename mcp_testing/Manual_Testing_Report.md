# Manual Testing Report - AI Scholarship Finder

Date: 2026-10-28    Application: Streamlit app (http://localhost:8501)    Browser: Chromium


## 1. Scope and approach

A tester follows each step by hand in the browser and records Actual result and Pass/Fail. Test data uses the app's fictional 'Demo data' mode so results are repeatable; Live search was checked separately.


## 2. Test cases

| ID | Steps | Expected result | Actual result (observed) | Status |
|---|---|---|---|---|
| MT-01 | Open app with 'streamlit run app.py' | Page loads with title and form | title='AI Scholarship Finder'; heading and Find button present; browser console errors: 0 | PASS |
| MT-02 | Choose Demo data, click Find scholarships | 8 fictional cards with DEMO label and banner | Demo banner shown; 8 cards labelled DEMO RECORD; 'Showing 8 of 8' present=True | PASS |
| MT-03 | Demo data, State=Karnataka, Category=OBC, search | Results shown, metrics update | Results rendered for Karnataka/OBC: Showing 8 of 8; eligibility metrics visible | PASS |
| MT-04 | Type 'Karnataka' in 'Provider / name contains' | List narrows | Text filter 'Karnataka' -> Showing 1 of 8 | PASS |
| MT-05 | Type nonsense in name filter | 'No results match these filters' | Warning 'No results match these filters' shown, no crash | PASS |
| MT-06 | Search with all fields blank | Warning about missing details | Warning about blank profile fields (potential matches only) displayed | PASS |
| MT-07 | Select income 'Exact amount' | Number input appears | DEFECT: after selecting 'Exact amount' the radio changed but the form still showed the 'Income range' dropdown; the 'Amount' box only appears after pressing Find (widgets inside st.form do not re-render until submit). See screenshot. | FAIL |
| MT-08 | Enter HTML/script text in Degree field and search | Shown as plain text, nothing runs | Hostile HTML in course field: injected elements in DOM = 0; no dialog/crash | PASS |
| MT-09 | Type 99 in Current year | No crash | Out-of-range year (99) did not crash the app (Streamlit enforces max 8) | PASS |
| MT-10 | Click 'Clear saved searches & AI cache' | 'Cleared.' appears | Confirmation 'Cleared.' shown | PASS |
| MT-11 | Resize browser to phone width | No horizontal scroll | Mobile 390px: scrollWidth=390, viewport=390 -> no horizontal scroll | PASS |
| MT-12 | Tick 'Save my search summary' | Search history panel appears | Opt-in history checkbox reveals the 'Search history' panel | PASS |
| MT-13 | Use Live search with Karnataka / UG / B.E. | Real results or clear problem messages | Live search finished: Showing 29 of 29; 1 problem(s) | PASS |
| MT-14 | Change 'Sort by' options | Order changes, no error | Not executed in this round | PENDING |
| MT-15 | Open a result card, check source link and red flags | Official source link, conditions and flags visible | Not executed in this round | PENDING |
| MT-16 | Click AI explanation on a card | Explanation shown or clear disabled/error message | Not executed in this round | PENDING |

Note: for MT-01 to MT-13 the actual results were observed by running the identical steps in the browser through MCP (see MCP Testing Report). MT-14 to MT-16 still need to be run by hand and ticked by the tester.


## 3. Bug log

| Ref | Item | Details |
|---|---|---|
| TC-08 | Income input mode switch | DEFECT: after selecting 'Exact amount' the radio changed but the form still showed the 'Income range' dropdown; the 'Amount' box only appears after pressing Find (widgets inside st.form do not re-render until submit). See screenshot. |


## 4. Observations

- Missing profile details only produce 'potential match' results - this is intended behavior.
- Hostile HTML typed in a text field was not rendered or executed.
- Mobile layout (390 px) has no horizontal scrolling.
- Live search needs internet and Node.js; its results vary with the real websites.

## 5. Manual vs MCP testing

| Aspect | Manual | MCP |
|---|---|---|
| Speed | Slow, repeated by a person | 16 cases in minutes |
| Repeatability | Varies by tester | Same steps every run |
| Evidence | Notes/screenshots by hand | Auto screenshots, page text, tool-call log |
| Judgement (look and feel) | Good | Limited to what is checked |
| Cost to set up | None | Needs Node.js + script |
