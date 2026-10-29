from __future__ import annotations

import streamlit as st

from models.evaluation import RedFlag, Severity
from ui.styles import esc, safe_http_url


def flag_html(f: RedFlag) -> str:
    basis = "Stated by source" if f.confirmed else "Inference / not stated"
    ev = f'<small>“{esc(f.evidence)}”</small>' if f.evidence else ""
    url = safe_http_url(f.source_url)
    src = f' · <a href="{esc(url)}" target="_blank" rel="noopener noreferrer">source</a>' if url else ""
    icon = {"critical": "⛔", "warning": "⚠️", "info": "ℹ️"}[f.severity.value]
    return (f'<div class="flag {f.severity.value}">{icon} <b>{esc(f.type.value)}</b> — {esc(f.explanation)}'
            f'{ev}<small>{basis}{src}</small></div>')


def render_flags(flags: list[RedFlag], limit: int | None = None) -> None:
    shown = flags if limit is None else flags[:limit]
    if not shown:
        st.caption("No red flags detected from the available information.")
        return
    st.markdown("".join(flag_html(f) for f in shown), unsafe_allow_html=True)


def important_flags(flags: list[RedFlag]) -> list[RedFlag]:
    return [f for f in flags if f.severity != Severity.INFO]
