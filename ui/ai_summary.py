"""On-demand Gemini explanation for one scholarship card. Never changes the deterministic status."""
from __future__ import annotations

import streamlit as st

from models.evaluation import GeminiSummary, RankedResult
from models.student import StudentProfile
from services.gemini_service import GeminiError, GeminiService
from ui.styles import md_escape


def student_context(p: StudentProfile) -> dict:
    """Only the eligibility-relevant fields; no identifiers exist in the profile to begin with."""
    return p.model_dump(mode="json", exclude={"preferences"}, exclude_none=True)


def _bullets(title: str, items: list[str]) -> None:
    if items:
        st.markdown(f"**{title}**\n" + "\n".join(f"- {md_escape(i)}" for i in items))


def render_summary(s: GeminiSummary) -> None:
    st.markdown(md_escape(s.summary))
    st.markdown("**In plain words:** " + md_escape(s.eligibility_explanation))
    if s.evidence_insufficient:
        st.warning("The extracted evidence is not sufficient for a complete explanation.")
    if s.grounding_warnings:
        st.warning("Some figures in this AI text could not be matched to the verified data — rely on the evidence panel:\n\n"
                   + "\n".join(f"- {md_escape(w)}" for w in s.grounding_warnings))
    a, b = st.columns(2)
    with a:
        _bullets("Confirmed facts", s.confirmed_facts)
        _bullets("Matching", s.matching_criteria)
        _bullets("Not met", s.unmet_criteria)
        _bullets("Unknown", s.unknown_criteria)
    with b:
        _bullets("AI interpretation (not confirmed)", s.interpretations)
        _bullets("Important restrictions", s.important_restrictions)
        _bullets("Documents", s.required_documents)
        _bullets("How to apply", s.application_instructions)
    _bullets("Red flags", s.red_flag_notes)
    _bullets("Next steps", s.next_steps)
    st.caption("AI-written explanation of the verified data above. It does not change eligibility and is not a prediction of selection.")


def make_explain_hook(service: GeminiService, profile: StudentProfile):
    def hook(r: RankedResult) -> None:
        sid = r.scholarship.id
        store = st.session_state.setdefault("ai_summaries", {})
        with st.expander("🤖 AI explanation (Gemini)"):
            if not service.available:
                st.info("Add GEMINI_API_KEY to your .env file to enable AI explanations.")
                return
            if st.button("Explain this scholarship", key=f"ai-{sid}"):
                with st.spinner("Asking Gemini…"):
                    try:
                        store[sid] = service.summarize(r.scholarship, r.evaluation, r.red_flags, student_context(profile))
                    except GeminiError as e:
                        store.pop(sid, None)
                        st.error(e.user_message)
            if sid in store:
                render_summary(store[sid])
    return hook
