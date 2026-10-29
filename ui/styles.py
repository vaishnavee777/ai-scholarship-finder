"""Shared CSS and small HTML helpers. All dynamic text is escaped: scraped content is untrusted."""
from __future__ import annotations

import re
from html import escape

import streamlit as st

CSS = """
<style>
:root { --ok:#15803d; --warn:#b45309; --bad:#b91c1c; --info:#1d4ed8; --muted:#64748b; --unv:#6b7280; --accent:#4f46e5; }
.block-container { padding-top: 1.6rem; max-width: 1180px; }
.hero { padding: 1.4rem 1.6rem; border-radius: 16px; margin-bottom: 1rem;
        background: linear-gradient(135deg, rgba(79,70,229,.14), rgba(14,165,233,.12)); border: 1px solid rgba(79,70,229,.25); }
.hero h1 { margin: 0 0 .25rem 0; font-size: 1.9rem; letter-spacing: -.02em; }
.hero p { margin: 0; opacity: .85; font-size: 1rem; }
.badge { display:inline-block; padding:.18rem .6rem; border-radius:999px; font-size:.78rem; font-weight:600; margin:0 .35rem .3rem 0; border:1px solid transparent; white-space:nowrap; }
.b-ok { color:var(--ok); background:rgba(21,128,61,.12); border-color:rgba(21,128,61,.35); }
.b-warn { color:var(--warn); background:rgba(217,119,6,.13); border-color:rgba(217,119,6,.4); }
.b-bad { color:var(--bad); background:rgba(185,28,28,.11); border-color:rgba(185,28,28,.35); }
.b-info { color:var(--info); background:rgba(29,78,216,.10); border-color:rgba(29,78,216,.3); }
.b-muted { color:var(--unv); background:rgba(107,114,128,.12); border-color:rgba(107,114,128,.3); }
.b-demo { color:#7e22ce; background:rgba(126,34,206,.12); border-color:rgba(126,34,206,.4); }
.sch-title { font-size:1.15rem; font-weight:700; margin:0; }
.sch-sub { color:var(--muted); font-size:.88rem; margin:.1rem 0 .5rem 0; }
.why { font-size:.84rem; color:var(--muted); margin:.2rem 0 .6rem 0; }
.crit { font-size:.9rem; margin:.1rem 0; }
.crit-h { font-weight:700; font-size:.82rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-bottom:.2rem; }
.flag { border-left:4px solid var(--info); padding:.35rem .7rem; margin:.3rem 0; border-radius:6px; background:rgba(100,116,139,.08); font-size:.88rem; }
.flag.warning { border-color:#d97706; background:rgba(217,119,6,.09); }
.flag.critical { border-color:var(--bad); background:rgba(185,28,28,.09); }
.flag small { color:var(--muted); display:block; }
.demo-banner { padding:.7rem 1rem; border-radius:10px; background:rgba(126,34,206,.10); border:1px solid rgba(126,34,206,.4); margin-bottom:.8rem; }
[data-testid="stMetricValue"] { font-size: 1.5rem; }
[data-testid="stMetricLabel"] p { font-size: .8rem; }
.meta { font-size:.78rem; color:var(--muted); }
@media (max-width: 640px) { .hero h1 { font-size:1.4rem; } .block-container { padding-left:1rem; padding-right:1rem; } }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value: object) -> str:
    """HTML-escape untrusted text; brackets become entities so markdown link syntax cannot form either."""
    return escape(str(value), quote=True).replace("[", "&#91;").replace("]", "&#93;")


def md_escape(value: object) -> str:
    """Neutralise markdown syntax in untrusted text rendered through st.markdown (links, images, emphasis, HTML)."""
    return re.sub(r"([\\`*_{}\[\]()#+!|<>~&$])", r"\\\1", str(value))


def badge(text: str, kind: str = "muted") -> str:
    return f'<span class="badge b-{kind}">{esc(text)}</span>'


def safe_http_url(url: str | None) -> str | None:
    """Only http(s) links are rendered as clickable; anything else from scraped data is dropped."""
    if url and url.lower().startswith(("http://", "https://")) and " " not in url:
        return url
    return None
