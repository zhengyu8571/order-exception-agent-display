"""Real PostgreSQL tests: upgrades must preserve data and fail atomically."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from order_agent.config import settings
from order_agent.domain.errors import MigrationError
from order_agent.persistence import migrations
from order_agent.persistence.database import connection

pytestmark = pytest.mark.integration


@pytest.fixture
def migration_database():
    # Each test owns its newly created database. Never drop/reset the demo DB.
    base = settings().database_url.get_secret_value()
    name = "orderflow_migration_" + uuid4().hex + "_test"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(base, dbname=name)
    finally:
        assert name.startswith("orderflow_migration_") and name.endswith("_test")
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_legacy_schema_adoption_preserves_domain_data(migration_database):
    dsn = migration_database
    with connection(dsn) as conn:
        # Reproduce the actual pre-migration CREATE IF NOT EXISTS startup schema.
        for component in migrations.COMPONENTS:
            conn.execute(migrations.load_migrations(component)[0].sql)
        conn.execute(
            "INSERT INTO business.orders(id,customer,required_quantity,due_days,allow_split,version,status,delivery_plan) "
            "VALUES('legacy-order','legacy-customer',100,5,false,7,'PLAN_APPLIED','{\"kept\":true}')"
        )
        conn.execute(
            "INSERT INTO business.operations(operation_id,order_id,payload_hash,result) "
            "VALUES('legacy-op','legacy-order','digest','{\"order_version\":7}')"
        )
        conn.execute(
            "INSERT INTO agent.tasks(id,order_id,status,planner_mode) "
            "VALUES('legacy-task','legacy-order','WAITING_APPROVAL','demo')"
        )
        conn.execute(
            "INSERT INTO agent.approvals(task_id,proposal_version,request_id,payload_hash,decision,decided_by) "
            "VALUES('legacy-task',3,'legacy-request','hash','{\"decision\":\"approve\"}','supervisor')"
        )
    assert migrations.apply_migrations("business", database_url=dsn) == [1, 2, 3]
    assert migrations.apply_migrations("agent", database_url=dsn) == [1, 2, 3]
    assert migrations.apply_migrations("business", database_url=dsn) == []
    with connection(dsn) as conn:
        order = conn.execute(
            "SELECT * FROM business.orders WHERE id='legacy-order'"
        ).fetchone()
        assert order["version"] == 7 and order["delivery_plan"] == {"kept": True}
        assert conn.execute(
            "SELECT result FROM business.operations WHERE operation_id='legacy-op'"
        ).fetchone()["result"] == {"order_version": 7}
        assert conn.execute(
            "SELECT decision FROM agent.approvals WHERE task_id='legacy-task'"
        ).fetchone()["decision"] == {"decision": "approve"}
        assert (
            conn.execute(
                "SELECT status FROM agent.tasks WHERE id='legacy-task'"
            ).fetchone()["status"]
            == "WAITING_APPROVAL"
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM orderflow_meta.schema_migrations"
            ).fetchone()["n"]
            == 6
        )


def test_concurrent_startups_apply_each_migration_once(migration_database):
    components = ["agent", "business", "agent", "business"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda component: migrations.apply_migrations(
                    component, database_url=migration_database
                ),
                components,
            )
        )
    assert sum(result == [1, 2, 3] for result in results) == 2
    assert sum(result == [] for result in results) == 2
    for component in migrations.COMPONENTS:
        assert (
            migrations.migration_status(component, database_url=migration_database)[0][
                "state"
            ]
            == "applied"
        )


def test_changed_migration_is_rejected(migration_database):
    migrations.apply_migrations("agent", database_url=migration_database)
    with connection(migration_database) as conn:
        conn.execute(
            "UPDATE orderflow_meta.schema_migrations SET checksum='edited' WHERE component='agent'"
        )
    with pytest.raises(MigrationError, match="was changed"):
        migrations.apply_migrations("agent", database_url=migration_database)
    assert (
        migrations.migration_status("agent", database_url=migration_database)[0][
            "state"
        ]
        == "modified"
    )


def test_newer_database_is_rejected(migration_database):
    migrations.apply_migrations("agent", database_url=migration_database)
    with connection(migration_database) as conn:
        conn.execute(
            "INSERT INTO orderflow_meta.schema_migrations(component,version,name,checksum) VALUES('agent',999,'999_future.sql','future')"
        )
    with pytest.raises(MigrationError, match="newer or unknown"):
        migrations.apply_migrations("agent", database_url=migration_database)


def test_failed_upgrade_rolls_back_all_pending_ddl_and_ledger(
    migration_database, monkeypatch
):
    migrations.apply_migrations("business", database_url=migration_database)
    existing = migrations.load_migrations("business")
    extra = [
        migrations.Migration(
            4, "004_probe.sql", "CREATE TABLE business.upgrade_probe(id int)"
        ),
        migrations.Migration(5, "005_failure.sql", "SELECT 1 / 0"),
    ]
    monkeypatch.setattr(
        migrations, "load_migrations", lambda component: existing + extra
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrations.apply_migrations("business", database_url=migration_database)
    with connection(migration_database) as conn:
        assert (
            conn.execute(
                "SELECT to_regclass('business.upgrade_probe') AS name"
            ).fetchone()["name"]
            is None
        )
        assert (
            conn.execute(
                "SELECT max(version) AS version FROM orderflow_meta.schema_migrations WHERE component='business'"
            ).fetchone()["version"]
            == 3
        )


def test_incompatible_legacy_schema_is_not_stamped(migration_database):
    with connection(migration_database) as conn:
        conn.execute(migrations.load_migrations("business")[0].sql)
        conn.execute(
            "ALTER TABLE business.orders ALTER COLUMN version TYPE text USING version::text"
        )
    with pytest.raises(
        MigrationError, match="Incompatible baseline column: business.orders.version"
    ):
        migrations.apply_migrations("business", database_url=migration_database)
    with connection(migration_database) as conn:
        assert (
            conn.execute(
                "SELECT to_regclass('orderflow_meta.schema_migrations') AS name"
            ).fetchone()["name"]
            is None
        )
