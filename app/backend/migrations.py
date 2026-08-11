"""Lightweight, idempotent schema migrations for SQLite.

`db.create_all()` creates missing tables but never alters existing ones.
This module adds columns that newer code expects to tables created by
older versions. All added columns must be nullable or have a default so
existing rows stay valid.
"""

from sqlalchemy import text

# table -> [(column_name, column_ddl)]
_MIGRATIONS = {
    "task": [
        ("machine_id", "INTEGER REFERENCES machine(id)"),
    ],
    "machine": [
        ("last_probe_error", "TEXT"),
    ],
    "api_token": [
        ("scope", "VARCHAR(20) DEFAULT 'full'"),
    ],
    "tool": [
        ("requires", "TEXT"),
        ("provider_id", "INTEGER REFERENCES tool_provider(id)"),
        ("settings_format", "VARCHAR(20) DEFAULT 'opaque'"),
        ("settings_schema", "TEXT"),
        ("inputs", "TEXT"),
        ("outputs", "TEXT"),
        ("tags", "TEXT"),
        ("version", "VARCHAR(50)"),
        ("available", "BOOLEAN DEFAULT 1"),
        ("config_hash", "VARCHAR(64)"),
    ],
}


def migrate(db):
    """Apply missing ALTER TABLE ADD COLUMN statements. Safe to run on
    every startup; must be called after db.create_all()."""
    with db.engine.connect() as conn:
        # Multiple daemon threads write to this SQLite DB; WAL avoids
        # writers blocking readers. The setting persists in the DB file.
        conn.execute(text("PRAGMA journal_mode=WAL"))

        for table, columns in _MIGRATIONS.items():
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {row[1] for row in rows}
            if not existing:
                # Table missing entirely; create_all is responsible for it.
                continue
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    print(f"[*] Migration: added column {table}.{name}")
        conn.commit()
