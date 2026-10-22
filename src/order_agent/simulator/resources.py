"""Normalized simulated business facts and consistent, optionally locked reads."""

from datetime import datetime
from zoneinfo import ZoneInfo

from psycopg import sql

from order_agent.domain.bindings import resource_fingerprint
from order_agent.domain.errors import BusinessRecordNotFound, BusinessUnavailable


def seed_resources(conn, order_id: str, context: dict):
    conn.execute(
        "INSERT INTO business.order_planning VALUES(%s,'SKU-DEMO',%s,%s,%s,1)",
        (
            order_id,
            context["as_of"],
            context["order"]["due_date"],
            context["local_quantity"],
        ),
    )
    for row in context["warehouses"]:
        conn.execute(
            "INSERT INTO business.warehouse_stock VALUES(%s,%s,%s,1)",
            (order_id, row["id"], row["available_quantity"]),
        )
        conn.execute(
            "INSERT INTO business.transfer_routes VALUES(%s,%s,%s,%s,1)",
            (order_id, row["id"], row["transfer_days"], row["unit_cost_cents"]),
        )
    for row in context["purchase_batches"]:
        conn.execute(
            "INSERT INTO business.purchase_batches VALUES(%s,%s,%s,%s,%s,1)",
            (order_id, row["id"], row["quantity"], row["arrival_date"], row["status"]),
        )
    policy = context["policy"]
    conn.execute(
        "INSERT INTO business.customer_policies VALUES(%s,%s,%s,%s,1)",
        (
            order_id,
            policy["allow_split"],
            policy["minimum_first_quantity"],
            policy["last_delivery_date"],
        ),
    )


def load_context(conn, order_id: str, *, lock=False) -> dict:
    # 只读调查由调用者开启一致性快照；执行时复用业务事务并锁行。
    # 不能在这里另开连接，否则校验锁会在订单写入前释放，留下竞争窗口。
    suffix = " FOR UPDATE" if lock else ""
    order = conn.execute(
        "SELECT * FROM business.orders WHERE id=%s" + suffix, (order_id,)
    ).fetchone()
    plan = conn.execute(
        "SELECT * FROM business.order_planning WHERE order_id=%s" + suffix, (order_id,)
    ).fetchone()
    policy = conn.execute(
        "SELECT * FROM business.customer_policies WHERE order_id=%s" + suffix,
        (order_id,),
    ).fetchone()
    if not order or not plan or not policy:
        raise BusinessRecordNotFound("订单或规划基础事实不存在")
    warehouses = conn.execute(
        "SELECT * FROM business.warehouse_stock WHERE order_id=%s ORDER BY warehouse_id"
        + suffix,
        (order_id,),
    ).fetchall()
    routes = conn.execute(
        "SELECT * FROM business.transfer_routes WHERE order_id=%s ORDER BY warehouse_id"
        + suffix,
        (order_id,),
    ).fetchall()
    batches = conn.execute(
        "SELECT * FROM business.purchase_batches WHERE order_id=%s ORDER BY batch_id"
        + suffix,
        (order_id,),
    ).fetchall()
    by_id = {row["warehouse_id"]: row for row in routes}
    if set(by_id) != {row["warehouse_id"] for row in warehouses}:
        raise BusinessUnavailable("仓库与运输路线数据不完整，停止规划")
    context = {
        "as_of": plan["as_of"].isoformat(),
        "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "order": {
            "id": order_id,
            "version": order["version"],
            "required_quantity": order["required_quantity"],
            "due_date": plan["due_date"].isoformat(),
        },
        "local_quantity": plan["local_quantity"],
        "local_version": plan["local_version"],
        "warehouses": [
            {
                "id": row["warehouse_id"],
                "available_quantity": row["available_quantity"],
                "version": row["version"],
                "transfer_days": by_id[row["warehouse_id"]]["transfer_days"],
                "unit_cost_cents": by_id[row["warehouse_id"]]["unit_cost_cents"],
                "route_version": by_id[row["warehouse_id"]]["version"],
            }
            for row in warehouses
        ],
        "purchase_batches": [
            {
                "id": row["batch_id"],
                "quantity": row["quantity"],
                "arrival_date": row["arrival_date"].isoformat(),
                "status": row["status"],
                "version": row["version"],
            }
            for row in batches
        ],
        "policy": {
            "allow_split": policy["allow_split"],
            "minimum_first_quantity": policy["minimum_first_quantity"],
            "last_delivery_date": policy["last_delivery_date"].isoformat(),
            "version": policy["version"],
        },
    }
    context["resource_digest"] = resource_fingerprint(context)
    return context


def change_resource(conn, order_id, body):
    """Only fixed table/field mappings; every mutation takes the same order-first lock."""
    # 所有资源变更先锁订单，和 apply_plan 保持相同锁顺序，减少死锁风险。
    if not conn.execute(
        "SELECT id FROM business.orders WHERE id=%s FOR UPDATE", (order_id,)
    ).fetchone():
        raise BusinessRecordNotFound("订单不存在")
    table, identity, version, mapping = {
        "warehouse": (
            "warehouse_stock",
            "warehouse_id",
            "version",
            {"quantity": "available_quantity"},
        ),
        "route": (
            "transfer_routes",
            "warehouse_id",
            "version",
            {"transfer_days": "transfer_days", "unit_cost_cents": "unit_cost_cents"},
        ),
        "batch": (
            "purchase_batches",
            "batch_id",
            "version",
            {"quantity": "quantity", "date": "arrival_date", "status": "status"},
        ),
        "policy": (
            "customer_policies",
            None,
            "version",
            {
                "quantity": "minimum_first_quantity",
                "allow_split": "allow_split",
                "date": "last_delivery_date",
            },
        ),
        "local": (
            "order_planning",
            None,
            "local_version",
            {"quantity": "local_quantity"},
        ),
        "order": ("order_planning", None, "local_version", {"date": "due_date"}),
    }[body.target]
    updates, parameters = [], []
    for field, column in mapping.items():
        value = getattr(body, field)
        if value is not None:
            updates.append(sql.SQL("{}=%s").format(sql.Identifier(column)))
            parameters.append(value)
    updates.append(
        sql.SQL("{}={}+1").format(sql.Identifier(version), sql.Identifier(version))
    )
    where = sql.SQL("order_id=%s")
    parameters.append(order_id)
    if identity:
        where += sql.SQL(" AND {}=%s").format(sql.Identifier(identity))
        parameters.append(body.resource_id)
    changed = conn.execute(
        sql.SQL("UPDATE business.{} SET {} WHERE {} RETURNING *").format(
            sql.Identifier(table), sql.SQL(",").join(updates), where
        ),
        parameters,
    ).fetchone()
    if not changed:
        raise BusinessRecordNotFound("当前订单没有该资源")
    if body.target == "order":
        conn.execute(
            "UPDATE business.orders SET version=version+1,updated_at=now() WHERE id=%s",
            (order_id,),
        )
    return {
        "changed": True,
        "target": body.target,
        "resource_id": body.resource_id,
        "resource_version": changed[version],
    }
