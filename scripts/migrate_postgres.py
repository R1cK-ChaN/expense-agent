#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATIONS_DIR = ROOT / "migrations"
MIGRATION_NAME_PATTERN = re.compile(r"^(?P<version>\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text()


def discover_migrations(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    if not migrations_dir.exists():
        raise SystemExit(f"migrations directory not found: {migrations_dir}")

    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_NAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise SystemExit(
                "migration filenames must match NNNN_description.sql: "
                f"{path.name}"
            )
        version = match.group("version")
        if version in versions:
            raise SystemExit(f"duplicate migration version: {version}")
        versions.add(version)
        migrations.append(Migration(version=version, name=path.name, path=path))

    if not migrations:
        raise SystemExit(f"no migrations found in {migrations_dir}")
    return migrations


def check_migrations(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    migrations = discover_migrations(migrations_dir)
    for migration in migrations:
        if not migration.sql.strip():
            raise SystemExit(f"empty migration: {migration.name}")
    return migrations


def apply_migrations(
    database_url: str,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> list[Migration]:
    migrations = check_migrations(migrations_dir)
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "psycopg is required to run migrations. "
            "Install project dependencies first."
        ) from exc

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists schema_migrations (
                    version text primary key,
                    name text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )
            cursor.execute("select version from schema_migrations")
            applied_versions = {row[0] for row in cursor.fetchall()}

            for migration in migrations:
                if migration.version in applied_versions:
                    continue
                cursor.execute(migration.sql)
                cursor.execute(
                    """
                    insert into schema_migrations (version, name)
                    values (%s, %s)
                    """,
                    (migration.version, migration.name),
                )

    return migrations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply Expense Agent PostgreSQL migrations.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate local migration files without connecting to PostgreSQL.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=DEFAULT_MIGRATIONS_DIR,
        help="Directory containing SQL migration files.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        migrations = check_migrations(args.migrations_dir)
        print(f"validated {len(migrations)} migration(s):")
        for migration in migrations:
            print(f"- {migration.name}")
        return 0

    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    migrations = apply_migrations(args.database_url, args.migrations_dir)
    print(f"applied migration set through {migrations[-1].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
