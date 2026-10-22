"""Versioned, transactional SQL migrations for the two application schemas.

Framework checkpoint tables remain exclusively owned by PostgresSaver.setup().
Application startup never drops a table or resets existing domain data.
"""

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import psycopg

from order_agent.domain.errors import MigrationError
from order_agent.persistence.database import connection

COMPONENTS = ("agent", "business")
MIGRATION_LOCK = "orderflow:application-schema-migrations"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        # Windows checkouts may use CRLF; line endings do not change SQL semantics.
        normalized = self.sql.replace("\r\n", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_migrations(component: str) -> list[Migration]:
    if component not in COMPONENTS:
        raise MigrationError(f"Unknown migration component: {component}")
    directory = files("order_agent.persistence").joinpath("sql", component)
    migrations = []
    for resource in sorted(directory.iterdir(), key=lambda item: item.name):
        match = re.fullmatch(r"(\d+)_([a-z0-9_]+)\.sql", resource.name)
        if not match:
            raise MigrationError(f"Invalid migration resource: {resource.name}")
        migrations.append(
            Migration(
                int(match[1]), resource.name, resource.read_text(encoding="utf-8")
            )
        )
    versions = [migration.version for migration in migrations]
    if not versions or versions != list(range(1, len(versions) + 1)):
        raise MigrationError(
            f"Migration versions must be contiguous from 1: {component}"
        )
    return migrations


# The v1 application used CREATE IF NOT EXISTS without a migration ledger.
# Verify required columns and keys before adopting those existing tables as v1.
# A mismatch fails atomically; it never silently stamps an incompatible schema.
BASELINE_COLUMNS = {
    "business": {
        "orders": {
            "id": "text",
            "customer": "text",
            "required_quantity": "int4",
            "due_days": "int4",
            "allow_split": "bool",
            "version": "int4",
            "status": "text",
            "delivery_plan": "jsonb",
            "updated_at": "timestamptz",
        },
        "stock": {
            "order_id": "text",
            "local_quantity": "int4",
            "remote_quantity": "int4",
            "transfer_days": "int4",
        },
        "arrivals": {"order_id": "text", "quantity": "int4", "arrival_days": "int4"},
        "operations": {
            "operation_id": "text",
            "order_id": "text",
            "payload_hash": "text",
            "result": "jsonb",
            "created_at": "timestamptz",
        },
    },
    "agent": {
        "tasks": {
            "id": "text",
            "order_id": "text",
            "status": "text",
            "planner_mode": "text",
            "fault": "text",
            "last_error": "text",
            "created_at": "timestamptz",
            "updated_at": "timestamptz",
        },
        "events": {
            "id": "int8",
            "task_id": "text",
            "type": "text",
            "data": "jsonb",
            "created_at": "timestamptz",
        },
        "approvals": {
            "task_id": "text",
            "proposal_version": "int4",
            "request_id": "text",
            "payload_hash": "text",
            "decision": "jsonb",
            "decided_by": "text",
            "created_at": "timestamptz",
        },
        "fault_hits": {"task_id": "text", "point": "text"},
    },
}
BASELINE_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    "business": {
        "orders": ("id",),
        "stock": ("order_id",),
        "arrivals": ("order_id",),
        "operations": ("operation_id",),
    },
    "agent": {
        "tasks": ("id",),
        "events": ("id",),
        "approvals": ("task_id", "proposal_version"),
        "fault_hits": ("task_id", "point"),
    },
}


def validate_baseline(conn: psycopg.Connection[dict[str, Any]], component: str) -> None:
    for table, columns in BASELINE_COLUMNS[component].items():
        rows = conn.execute(
            "SELECT column_name, udt_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            (component, table),
        ).fetchall()
        actual = {row["column_name"]: row for row in rows}
        nullable = {"delivery_plan", "last_error"}
        for name, sql_type in columns.items():
            found = actual.get(name)
            if not found or found["udt_name"] != sql_type:
                raise MigrationError(
                    f"Incompatible baseline column: {component}.{table}.{name}"
                )
            if name not in nullable and found["is_nullable"] != "NO":
                raise MigrationError(
                    f"Baseline requires NOT NULL: {component}.{table}.{name}"
                )
        keys = conn.execute(
            "SELECT a.attname FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum,ord) "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum "
            "WHERE n.nspname=%s AND t.relname=%s AND c.contype='p' ORDER BY k.ord",
            (component, table),
        ).fetchall()
        if tuple(row["attname"] for row in keys) != BASELINE_KEYS[component][table]:
            raise MigrationError(
                f"Incompatible baseline primary key: {component}.{table}"
            )
    if component == "agent":
        unique = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='agent' AND t.relname='approvals' AND c.contype='u'"
        ).fetchall()
        if not any(
            row["definition"] == "UNIQUE (task_id, request_id)" for row in unique
        ):
            raise MigrationError("Baseline approvals require unique task_id/request_id")


def apply_migrations(component: str, *, database_url: str | None = None) -> list[int]:
    migrations = load_migrations(component)
    with connection(database_url) as conn:
        conn.execute("SET LOCAL lock_timeout = '10s'")
        # Both FastAPI processes may start together. Acquire before metadata DDL.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,17))", (MIGRATION_LOCK,)
        )
        conn.execute("CREATE SCHEMA IF NOT EXISTS orderflow_meta")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orderflow_meta.schema_migrations (
                component text NOT NULL, version integer NOT NULL, name text NOT NULL,
                checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(component,version)
            )
        """)
        rows = conn.execute(
            "SELECT version,name,checksum FROM orderflow_meta.schema_migrations "
            "WHERE component=%s ORDER BY version",
            (component,),
        ).fetchall()
        applied = {row["version"]: row for row in rows}
        known = {migration.version: migration for migration in migrations}
        if set(applied) - set(known):
            raise MigrationError(
                f"Database has newer or unknown migrations: {component}"
            )
        if list(applied) != list(range(1, len(applied) + 1)):
            raise MigrationError(f"Database migration history has gaps: {component}")
        for version, row in applied.items():
            migration = known[version]
            if row["checksum"] != migration.checksum or row["name"] != migration.name:
                raise MigrationError(
                    f"Applied migration was changed: {component}/{version}"
                )
        added = []
        for migration in migrations:
            if migration.version in applied:
                continue
            conn.execute(migration.sql)
            if migration.version == 1:
                validate_baseline(conn, component)
            conn.execute(
                "INSERT INTO orderflow_meta.schema_migrations(component,version,name,checksum) "
                "VALUES(%s,%s,%s,%s)",
                (component, migration.version, migration.name, migration.checksum),
            )
            added.append(migration.version)
        # All pending SQL and ledger rows commit together on context exit.
    return added


def migration_status(component: str, *, database_url: str | None = None) -> list[dict]:
    migrations = load_migrations(component)
    with connection(database_url) as conn:
        exists = conn.execute(
            "SELECT to_regclass('orderflow_meta.schema_migrations') AS name"
        ).fetchone()
        rows = (
            conn.execute(
                "SELECT version,name,checksum,applied_at FROM orderflow_meta.schema_migrations "
                "WHERE component=%s ORDER BY version",
                (component,),
            ).fetchall()
            if exists and exists["name"]
            else []
        )
    applied = {row["version"]: row for row in rows}
    result = []
    for migration in migrations:
        row = applied.pop(migration.version, None)
        state = "pending"
        if row:
            state = (
                "applied"
                if row["checksum"] == migration.checksum
                and row["name"] == migration.name
                else "modified"
            )
        result.append(
            {
                "component": component,
                "version": migration.version,
                "name": migration.name,
                "state": state,
            }
        )
    result.extend(
        {"component": component, "version": v, "name": row["name"], "state": "unknown"}
        for v, row in applied.items()
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect/apply OrderFlow application migrations"
    )
    parser.add_argument("action", choices=("status", "apply"))
    parser.add_argument("--component", choices=(*COMPONENTS, "all"), default="all")
    args = parser.parse_args()
    components = COMPONENTS if args.component == "all" else (args.component,)
    result = []
    for component in components:
        if args.action == "apply":
            apply_migrations(component)
        result.extend(migration_status(component))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(row["state"] in {"modified", "unknown"} for row in result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
