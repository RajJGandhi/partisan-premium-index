# Database migrations

Run the idempotent migration command for both fresh and existing installations:

```bash
PYTHONPATH=. python scripts/migrate_db.py
```

The runner uses SQLAlchemy metadata to create the complete schema and applies additive, non-destructive columns and indexes to compatible legacy SQLite/PostgreSQL databases. Back up an existing database before migration. The reference file `001_ppi_core.sql` documents the scope; `scripts/migrate_db.py` is the executable cross-database migration.
