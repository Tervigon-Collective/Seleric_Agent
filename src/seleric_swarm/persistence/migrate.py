"""Small ordered SQL migration runner for Seleric's Postgres schema."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from seleric_swarm.config.settings import get_settings
from seleric_swarm.paths import repo_root


def run_migrations(database_url: str, migrations_dir: Path | None = None) -> list[str]:
    if not database_url.strip():
        raise ValueError("database_url must not be empty")
    directory = migrations_dir or repo_root() / "migrations"
    engine = create_engine(database_url, pool_pre_ping=True)
    applied: list[str] = []
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(hashtext('seleric-schema-migrations'))"))
        conn.commit()
        try:
            _run_locked(conn, directory, applied)
        finally:
            conn.execute(
                text("SELECT pg_advisory_unlock(hashtext('seleric-schema-migrations'))")
            )
            conn.commit()
    engine.dispose()
    return applied


def _run_locked(conn: Connection, directory: Path, applied: list[str]) -> None:
    with conn.begin():
        conn.execute(
            text(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum_sha256 TEXT,
                dirty BOOLEAN NOT NULL DEFAULT FALSE,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
        )
        conn.execute(
            text("ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum_sha256 TEXT")
        )
        conn.execute(
            text(
                "ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS "
                "dirty BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
    paths = sorted(directory.glob("*.sql"))
    checksums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    rows = conn.execute(
        text("SELECT version, checksum_sha256, dirty FROM schema_migrations")
    ).mappings().all()
    conn.commit()
    known = {str(row["version"]): row for row in rows}
    dirty = [version for version, row in known.items() if row["dirty"]]
    if dirty:
        raise RuntimeError(f"dirty migration state requires repair: {', '.join(dirty)}")
    for version, row in known.items():
        expected = checksums.get(version)
        recorded = row["checksum_sha256"]
        if expected is not None and recorded is not None and recorded != expected:
            raise RuntimeError(f"migration checksum mismatch: {version}")
        if expected is not None and recorded is None:
            with conn.begin():
                conn.execute(
                    text(
                        """UPDATE schema_migrations SET checksum_sha256=:checksum
                        WHERE version=:version AND checksum_sha256 IS NULL"""
                    ),
                    {"version": version, "checksum": expected},
                )
    for path in paths:
        if path.name in known:
            continue
        checksum = checksums[path.name]
        with conn.begin():
            conn.execute(
                text(
                    """INSERT INTO schema_migrations(version, checksum_sha256, dirty)
                    VALUES (:version, :checksum, TRUE)"""
                ),
                {"version": path.name, "checksum": checksum},
            )
        try:
            with conn.begin():
                # psycopg interprets PL/pgSQL RAISE '%' as a DBAPI placeholder
                # when SQLAlchemy supplies an empty parameter mapping. Execute
                # scripts without parameters through the driver connection.
                driver = conn.connection.driver_connection
                if driver is None:
                    raise RuntimeError("database driver connection is unavailable")
                driver.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    text(
                        """UPDATE schema_migrations SET dirty=FALSE, applied_at=NOW()
                        WHERE version=:version"""
                    ),
                    {"version": path.name},
                )
        except Exception:
            conn.rollback()
            raise
        else:
            applied.append(path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply pending Seleric SQL migrations")
    parser.add_argument("--database-url", default=get_settings().database_url)
    args = parser.parse_args()
    for version in run_migrations(args.database_url):
        print(version)


if __name__ == "__main__":
    main()
