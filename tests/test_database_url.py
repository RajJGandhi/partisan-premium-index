from app.db.database import _normalize_database_url


def test_managed_postgres_urls_use_installed_psycopg3_driver():
    assert _normalize_database_url("postgresql://user:pass@host/db") == ("postgresql+psycopg://user:pass@host/db")
    assert _normalize_database_url("postgres://user:pass@host/db") == ("postgresql+psycopg://user:pass@host/db")
    assert _normalize_database_url("sqlite:///data/test.db") == "sqlite:///data/test.db"
