"""AI Scholarship Finder — Streamlit entry point."""
from __future__ import annotations

import streamlit as st

from config.settings import get_settings
from database.repository import ScholarshipRepository
from models.student import StudentProfile
from services.demo_loader import load_demo_scholarships
from services.ranking_service import build_results
from services.gemini_service import GeminiService
from ui.ai_summary import make_explain_hook
from ui.profile_form import render_profile_form
from ui.results import render_results
from ui.styles import inject_css

st.set_page_config(page_title="AI Scholarship Finder", page_icon="🎓", layout="wide")
inject_css()
settings = get_settings()


@st.cache_resource
def get_repo() -> ScholarshipRepository:
    return ScholarshipRepository(settings.db_path)


repo = get_repo()
ss = st.session_state

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Data source", ["Live search", "Demo data"], help="Demo data is fictional and only for trying the interface.")
    save_history = st.checkbox("Save my search summary on this device", value=False,
                               help="Stores only course, level and state, never income or other details.")
    force = st.checkbox("Force fresh verification", value=False, help="Ignore cached results and re-open the sources.")
    st.divider()
    st.caption("Gemini: " + ("configured ✅" if settings.has_gemini_key else "no GEMINI_API_KEY set — AI summaries disabled"))
    if st.button("Clear saved searches & AI cache"):
        repo.clear_search_data()
        ss.pop("search", None)
        st.success("Cleared.")
    if save_history:
        with st.expander("Search history"):
            for h in repo.list_history():
                st.caption(f"{h['created_at'][:16]} — {h['summary']} ({h['result_count']} results)")

st.markdown(
    '<div class="hero"><h1>🎓 AI Scholarship Finder</h1>'
    "<p>Find Indian scholarships you may qualify for — with the exact conditions, red flags and official sources. "
    "Accuracy and source verification come before the number of results.</p></div>",
    unsafe_allow_html=True,
)

def run_search(profile: StudentProfile, force_refresh: bool) -> dict:
    from services.search_orchestrator import run_live_search

    bar = st.progress(0.0, text="Starting…")
    result = run_live_search(
        profile, repo, settings, force_refresh=force_refresh, save_history=save_history,
        progress=lambda frac, msg: bar.progress(min(max(frac, 0.0), 1.0), text=msg),
    )
    bar.empty()
    result["profile"] = profile
    return result


profile, submitted = render_profile_form()

if submitted:
    missing = profile.missing_fields()
    if mode == "Demo data":
        ss["search"] = {"live": False, "scholarships": load_demo_scholarships(), "errors": [], "profile": profile, "notes": []}
    else:
        ss["search"] = run_search(profile, force)

search = ss.get("search")
if not search:
    st.info("Fill in your profile (everything is optional) and press **Find scholarships**.")
else:
    prof: StudentProfile = search["profile"]
    if not search["live"]:
        st.markdown('<div class="demo-banner"><b>Demo data.</b> These records are fictional and exist only to show how the '
                    "app works. They are not live search results.</div>", unsafe_allow_html=True)
    missing = prof.missing_fields()
    if missing:
        st.warning("Some details were left blank, so some results can only be **potential matches**: " + ", ".join(missing) + ".")
    for n in search.get("notes", []):
        st.caption(n)
    if search["live"] and search["errors"]:
        with st.expander(f"⚠️ {len(search['errors'])} problem(s) during the search", expanded=not search["scholarships"]):
            for e in search["errors"]:
                st.write("•", e)
            if st.button("🔄 Retry search with fresh verification"):
                ss["search"] = run_search(search["profile"], True)
                st.rerun()
    results = build_results(prof, search["scholarships"], freshness_days=settings.freshness_days)
    gemini = GeminiService(settings, repo)
    render_results(results, search["live"], search["errors"], make_explain_hook(gemini, prof))
