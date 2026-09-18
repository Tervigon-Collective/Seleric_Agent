"""Small ordered SQL migration runner for Seleric's Postgres schema."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine, text

from seleric_swarm.config.settings import get_settings
from seleric_swarm.paths import repo_root


def run_migrations(database_url: str, migrations_dir: Path | None = None) -> list[str]:
    if not database_url.strip():
        raise ValueError("database_url must not be empty")
    directory = migrations_dir or repo_root() / "migrations"
    engine = create_engine(database_url)
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(
            text(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
        )
        known = set(conn.execute(text("SELECT version FROM schema_migrations")).scalars())
        for path in sorted(directory.glob("*.sql")):
            if path.name in known:
                continue
            # Escape % so psycopg/SQLAlchemy pyformat does not treat PL/pgSQL
            # RAISE placeholders (e.g. '% is append-only') as bind markers.
            conn.exec_driver_sql(path.read_text(encoding="utf-8").replace("%", "%%"))
            conn.execute(
                text("INSERT INTO schema_migrations(version) VALUES (:version)"),
                {"version": path.name},
            )
            applied.append(path.name)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply pending Seleric SQL migrations")
    parser.add_argument("--database-url", default=get_settings().database_url)
    args = parser.parse_args()
    for version in run_migrations(args.database_url):
        print(version)


if __name__ == "__main__":
    main()
