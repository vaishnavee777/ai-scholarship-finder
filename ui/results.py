from __future__ import annotations

from datetime import date
from typing import Callable

import streamlit as st

from models.evaluation import EligibilityStatus, RankedResult
from models.scholarship import ApplicationStatus, ProviderType
from services.ranking_service import filter_results
from ui.scholarship_card import render_card

SORTS = {
    "Best match (relevance score)": lambda r: (-r.score, r.scholarship.name),
    "Deadline: soonest first": lambda r: (r.scholarship.deadline is None, r.scholarship.deadline or date.max),
    "Award amount: highest first": lambda r: (-(r.scholarship.award_amount or 0), r.scholarship.name),
    "Name (A–Z)": lambda r: r.scholarship.name.lower(),
}


def render_metrics(results: list[RankedResult]) -> None:
    count = lambda st_: sum(r.evaluation.status == st_ for r in results)
    cols = st.columns(6)
    cols[0].metric("Discovered", len(results))
    cols[1].metric("Eligible", count(EligibilityStatus.ELIGIBLE))
    cols[2].metric("Potential", count(EligibilityStatus.POTENTIAL_MATCH))
    cols[3].metric("Likely ineligible", count(EligibilityStatus.LIKELY_INELIGIBLE))
    cols[4].metric("Unverified", count(EligibilityStatus.UNVERIFIED))
    cols[5].metric("Open now", sum(r.evaluation.application_status == ApplicationStatus.OPEN for r in results))


def render_empty_state(live: bool, errors: list[str]) -> None:
    st.info(
        "**No scholarships to show.**\n\n"
        + ("The search finished without records that passed verification, or none matched your preferences. "
           "Try removing preference filters, widening the course / level, or re-running with **Force fresh verification**."
           if live else "Demo mode has no matching records for these filters.")
    )
    for e in errors[:5]:
        st.caption(f"• {e}")


def render_results(
    results: list[RankedResult], live: bool, errors: list[str],
    explain_hook: Callable[[RankedResult], None] | None = None,
) -> None:
    render_metrics(results)
    if not results:
        render_empty_state(live, errors)
        return
    with st.expander("Sort & filter", expanded=True):
        a, b, c = st.columns(3)
        sort = a.selectbox("Sort by", list(SORTS))
        statuses = b.multiselect("Eligibility status", [s.value for s in EligibilityStatus], placeholder="All")
        ptypes = c.multiselect("Provider type", [p.value for p in ProviderType], placeholder="All")
        d, e, f, g = st.columns(4)
        min_amt = d.number_input("Min award (₹)", min_value=0, value=0, step=5_000)
        use_deadline = e.checkbox("Deadline before…")
        deadline = e.date_input("Date", value=date.today(), label_visibility="collapsed", disabled=not use_deadline)
        open_only = f.checkbox("Open applications only")
        query = g.text_input("Provider / name contains")
    shown = filter_results(
        results,
        statuses={EligibilityStatus(s) for s in statuses} or None,
        provider_types={ProviderType(p) for p in ptypes} or None,
        min_amount=int(min_amt) or None,
        deadline_before=deadline if use_deadline else None,
        open_only=open_only, provider_query=query,
    )
    shown.sort(key=SORTS[sort])
    st.caption(f"Showing {len(shown)} of {len(results)}")
    if not shown:
        st.warning("No results match these filters.")
    for r in shown:
        render_card(r, explain_hook)
