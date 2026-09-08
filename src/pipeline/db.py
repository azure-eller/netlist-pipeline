"""Connections and forward-only SQL migrations."""

from pathlib import Path

import psycopg

from pipeline.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url)


def migrate() -> list[str]:
    """Apply every migrations/NNN_*.sql not yet recorded. Returns names applied."""
    applied: list[str] = []
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "create table if not exists schema_migrations "
            "(name text primary key, applied_at timestamptz not null default now())"
        )
        cur.execute("select name from schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            cur.execute(path.read_text())
            cur.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied


if __name__ == "__main__":
    print("applied:", migrate())
