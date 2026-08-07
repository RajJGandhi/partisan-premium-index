from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "dashboard" / "streamlit_app.py"


def test_public_dashboard_boots_without_keys():
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    assert not app.exception
    assert any("Partisan Premium Index" in x.value for x in app.markdown)
