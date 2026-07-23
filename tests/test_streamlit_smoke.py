from streamlit.testing.v1 import AppTest


def test_public_dashboard_boots_without_keys():
    app = AppTest.from_file("app/dashboard/streamlit_app.py", default_timeout=30)
    app.run()
    assert not app.exception
    assert any("Partisan Premium Index" in x.value for x in app.markdown)
