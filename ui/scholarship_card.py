from __future__ import annotations

from datetime import datetime
from typing import Callable

import streamlit as st

from models.evaluation import CriterionOutcome, EligibilityStatus, RankedResult
from models.scholarship import ApplicationStatus, SourceReliability
from services.red_flag_detector import describe_award_count
from ui.red_flags import important_flags, render_flags
from ui.styles import badge, esc, md_escape, safe_http_url

_ELIG_BADGE = {
    EligibilityStatus.ELIGIBLE: ("✅ Eligible", "ok"),
    EligibilityStatus.POTENTIAL_MATCH: ("🟡 Potential match", "warn"),
    EligibilityStatus.LIKELY_INELIGIBLE: ("❌ Likely ineligible", "bad"),
    EligibilityStatus.UNVERIFIED: ("❔ Unverified", "muted"),
}
_APP_BADGE = {
    ApplicationStatus.OPEN: ("Applications open", "ok"),
    ApplicationStatus.UPCOMING: ("Opens soon", "info"),
    ApplicationStatus.CLOSED: ("Applications closed", "bad"),
    ApplicationStatus.UNKNOWN: ("Application status unknown", "muted"),
}


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y, %H:%M UTC") if dt else "never"


def _inr(n: int) -> str:
    return f"₹{n:,}"


def render_card(r: RankedResult, explain_hook: Callable[[RankedResult], None] | None = None) -> None:
    s, ev = r.scholarship, r.evaluation
    with st.container(border=True):
        badges = [badge(*_ELIG_BADGE[ev.status]), badge(*_APP_BADGE[ev.application_status])]
        if s.is_demo:
            badges.append(badge("DEMO RECORD — not a real scholarship", "demo"))
        rel_kind = "ok" if s.source_reliability.is_authoritative else "muted"
        badges.append(badge(("🔒 " if s.source_reliability.is_authoritative else "") + s.source_reliability.value, rel_kind))
        sub = " · ".join(esc(x) for x in (s.provider, s.provider_type.value, f"AY {s.academic_year}" if s.academic_year else None) if x)
        st.markdown(f'<p class="sch-title">{esc(s.name)}</p><p class="sch-sub">{sub}</p>{"".join(badges)}', unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Award", _inr(s.award_amount) if s.award_amount else "Not published",
                  help=s.award_amount_text and f"Source wording: {s.award_amount_text}")
        c2.metric("Deadline", f"{s.deadline:%d %b %Y}" if s.deadline else "Not found")
        if s.award_count:
            c3.metric(f"Awards · scope: {s.award_count_scope.value.lower()}", f"{s.award_count:,}", help=describe_award_count(s))
        else:
            c3.metric("Awards available", "Not verified", help=describe_award_count(s))

        st.markdown(f'<div class="why">Why this is shown (not a chance of winning): {esc("; ".join(r.reasons))} · relevance score {r.score:g}/100</div>',
                    unsafe_allow_html=True)
        st.markdown(f"**Eligibility:** {md_escape(ev.explanation)}")
        if ev.application_note:
            st.caption(ev.application_note)

        m, u, k = st.columns(3)
        for col, title, items, icon in (
            (m, "Matching", ev.met, "✓"), (u, "Not met", ev.unmet, "✗"), (k, "Missing / unknown", ev.unknown, "?"),
        ):
            body = "".join(f'<div class="crit">{icon} {esc(c.requirement)}</div>' for c in items) or '<div class="crit">—</div>'
            col.markdown(f'<div class="crit-h">{title}</div>{body}', unsafe_allow_html=True)

        important = important_flags(r.red_flags)
        if important:
            st.markdown("**Important red flags**")
            render_flags(important, limit=3)
        if s.required_documents:
            st.markdown("**Documents:** " + ", ".join(md_escape(d) for d in s.required_documents))
        else:
            st.caption("Required documents: not listed in the source.")

        links = [(label, safe_http_url(url)) for label, url in (("Apply on official site", s.application_url), ("Guidelines", s.guideline_url))]
        cols = st.columns(max(1, len([l for l in links if l[1]])) + 1)
        for col, (label, url) in zip(cols, [l for l in links if l[1]]):
            col.link_button(label, url, use_container_width=True)
        st.markdown(f'<div class="meta">Retrieved: {_fmt_dt(s.retrieved_at)} · Last verified: {_fmt_dt(s.last_verified_at)} · '
                    f'Extraction confidence {s.extraction_confidence:.0%}</div>', unsafe_allow_html=True)

        with st.expander("Full eligibility details"):
            if not ev.criteria:
                st.write("No criteria could be extracted.")
            for c in ev.criteria:
                icon = {CriterionOutcome.MET: "✅", CriterionOutcome.NOT_MET: "❌", CriterionOutcome.UNKNOWN: "❔"}[c.outcome]
                st.markdown(f"{icon} **{md_escape(c.criterion)}** — requirement: {md_escape(c.requirement)}"
                            f"{' · you: ' + md_escape(c.student_value) if c.student_value else ''}  \n{md_escape(c.detail)}")
            if s.unresolved_fields:
                st.caption("Could not be determined from the source: " + ", ".join(s.unresolved_fields))
            st.markdown("**All red flags**")
            render_flags(r.red_flags)
        with st.expander(f"Supporting evidence ({len(s.evidence)})"):
            if not s.evidence:
                st.write("No field-level evidence was recorded.")
            for e in s.evidence:
                url = safe_http_url(e.source_url)
                link = f" — [source]({url.replace(')', '%29').replace('(', '%28')})" if url else ""
                st.markdown(f"**{md_escape(e.field)}**: “{md_escape(e.quote)}”{link}")
            for u in s.source_urls:
                if safe_http_url(u):
                    st.caption(f"Source: {u}")
        if explain_hook:
            explain_hook(r)
