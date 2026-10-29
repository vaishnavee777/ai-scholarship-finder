from __future__ import annotations

import streamlit as st

from models.student import (
    ApplicationType, Category, EducationLevel, Gender, ScholarshipBasis, ScholarshipKind,
    SearchPreferences, StudentProfile,
)

STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]
INCOME_RANGES = {
    "Up to ₹1 lakh": (0, 100_000),
    "₹1 lakh – ₹2.5 lakh": (100_001, 250_000),
    "₹2.5 lakh – ₹5 lakh": (250_001, 500_000),
    "₹5 lakh – ₹8 lakh": (500_001, 800_000),
    "Above ₹8 lakh": (800_001, None),
}
INSTITUTION_TYPES = ["Government", "Government-aided", "Private", "Deemed university", "Autonomous"]
NONE = "— Not specified —"


def _opt(label: str, options: list[str], key: str, help: str | None = None) -> str | None:
    v = st.selectbox(label, [NONE, *options], key=key, help=help)
    return None if v == NONE else v


def render_profile_form() -> tuple[StudentProfile, bool]:
    """Returns (profile, submitted). Every field is optional; missing ones lower certainty, not eligibility."""
    with st.form("profile"):
        st.subheader("Your profile")
        st.caption("Leave anything blank if you prefer. Missing details only make results less certain. "
                   "We never ask for Aadhaar, bank details or documents, and your profile is not stored.")
        a, b = st.columns(2)
        with a:
            st.markdown("**Personal**")
            state = _opt("State of residence / domicile", STATES, "state")
            category = _opt("Category", [c.value for c in Category], "category")
            income_mode = st.radio("Annual family income", ["Exact amount", "Select a range", "Prefer not to say"],
                                   horizontal=True, index=1, key="income_mode")
            income, rng = None, None
            if income_mode == "Exact amount":
                v = st.number_input("Amount (₹ per year)", min_value=0, max_value=100_000_000, value=None,
                                    step=10_000, placeholder="e.g. 250000", key="income")
                income = None if v is None else int(v)
            elif income_mode == "Select a range":
                label = st.selectbox("Income range", list(INCOME_RANGES), key="income_range")
                rng = INCOME_RANGES[label]
            with st.expander("Only if a scheme asks (optional)"):
                gender = _opt("Gender", [g.value for g in Gender], "gender",
                              help="Used only for schemes that explicitly restrict by gender.")
                disability = _opt("Person with disability", ["Yes", "No"], "disability",
                                  help="Used only for schemes meant for students with disabilities.")
        with b:
            st.markdown("**Academic**")
            level = _opt("Education level", [l.value for l in EducationLevel], "level")
            course = st.text_input("Degree / course", placeholder="e.g. B.E., B.Tech, B.Sc, B.Com", key="course").strip() or None
            c1, c2 = st.columns(2)
            with c1:
                year = st.number_input("Current year", min_value=1, max_value=8, value=None, key="year")
            with c2:
                app_type = _opt("Applying", [t.value for t in ApplicationType], "app_type")
            college = st.text_input("College / university", key="college").strip() or None
            inst_type = _opt("Institution type", INSTITUTION_TYPES, "inst_type")
            m1, m2 = st.columns(2)
            with m1:
                pct = st.number_input("Previous exam %", min_value=0.0, max_value=100.0, value=None, step=0.5, key="pct")
            with m2:
                cgpa = st.number_input("Previous CGPA (/10)", min_value=0.0, max_value=10.0, value=None, step=0.1, key="cgpa")

        st.markdown("**Search preferences**")
        p1, p2 = st.columns(2)
        with p1:
            kinds = st.multiselect("Provider type", [k.value for k in ScholarshipKind], key="kinds", placeholder="All providers")
            bases = st.multiselect("Basis", [b.value for b in ScholarshipBasis], key="bases", placeholder="All types")
        with p2:
            open_only = st.checkbox("Only show currently open applications", key="open_only")
            min_amt = st.number_input("Minimum award (₹, when published)", min_value=0, value=0, step=5_000, key="min_amt")
        submitted = st.form_submit_button("🔍 Find scholarships", type="primary", use_container_width=True)

    profile = StudentProfile(
        state=state, category=category, annual_family_income=income,
        income_range_low=rng[0] if rng else None, income_range_high=rng[1] if rng else None,
        gender=gender, has_disability=None if disability is None else disability == "Yes",
        education_level=level, course=course, current_year=None if year is None else int(year),
        institution_name=college, institution_type=inst_type,
        previous_percentage=None if pct is None else float(pct),
        previous_cgpa=None if cgpa is None else float(cgpa),
        application_type=app_type,
        preferences=SearchPreferences(
            kinds=[ScholarshipKind(k) for k in kinds], bases=[ScholarshipBasis(x) for x in bases],
            open_only=open_only, min_amount=int(min_amt) or None),
    )
    return profile, submitted
