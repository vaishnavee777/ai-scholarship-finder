from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_boots_without_exceptions():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Find scholarships" in b.label for b in at.button) or at.main


def test_demo_flow_renders_labelled_cards_and_metrics():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.radio[0].set_value("Demo data")
    at.selectbox(key="state").set_value("Karnataka")
    at.selectbox(key="category").set_value("OBC")
    at.run()
    next(b for b in at.button if "Find scholarships" in b.label).click()
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Demo data." in html and "DEMO RECORD" in html
    assert len(at.metric) >= 6


def test_ai_section_without_key_is_disabled_not_broken(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("config.settings.load_dotenv", lambda *a, **k: False)  # ignore the real .env
    from config.settings import get_settings
    get_settings.cache_clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.radio[0].set_value("Demo data")
    at.run()
    next(b for b in at.button if "Find scholarships" in b.label).click()
    at.run()
    assert not at.exception
    assert any("GEMINI_API_KEY" in i.value for i in at.info)
    get_settings.cache_clear()
