"""Small explicit business fixtures for investigating decisions, not ERP fulfillment."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from order_agent.domain.errors import BusinessRecordNotFound
from order_agent.persistence.database import connection


def build_context(
    order: dict, stock: dict, arrivals: dict, profile: str | None = None
) -> dict:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()

    def day(offset):
        return (today + timedelta(days=offset)).isoformat()

    warehouses = [
        {
            "id": "WH-002",
            "available_quantity": stock["remote_quantity"],
            "transfer_days": stock["transfer_days"],
            "unit_cost_cents": 200,
        }
    ]
    batches = [
        {
            "id": "PO-003",
            "quantity": arrivals["quantity"],
            "arrival_date": day(arrivals["arrival_days"]),
            "status": "EXPECTED",
        }
    ]
    allow_split = order["allow_split"]
    if profile in {"A", "F"}:
        warehouses = [
            {
                "id": "WH-002",
                "available_quantity": 25,
                "transfer_days": 1,
                "unit_cost_cents": 500,
            },
            {
                "id": "WH-003",
                "available_quantity": 40,
                "transfer_days": 3,
                "unit_cost_cents": 100,
            },
        ]
        batches[0]["arrival_date"] = day(8)
    elif profile in {"B", "C"}:
        warehouses = [
            {
                "id": "WH-002",
                "available_quantity": 10,
                "transfer_days": 7,
                "unit_cost_cents": 200,
            }
        ]
        batches = [
            {
                "id": "PO-003",
                "quantity": 20,
                "arrival_date": day(2),
                "status": "EXPECTED",
            },
            {
                "id": "PO-004",
                "quantity": 20,
                "arrival_date": day(8),
                "status": "EXPECTED",
            },
        ]
        allow_split = profile == "B"
    elif profile == "E":
        warehouses[0]["available_quantity"] = 5
        batches[0]["quantity"] = 5
    return {
        "as_of": today.isoformat(),
        "today": today.isoformat(),
        "order": {
            "id": order["id"],
            "version": order["version"],
            "required_quantity": order["required_quantity"],
            "due_date": day(order["due_days"]),
        },
        "local_quantity": stock["local_quantity"],
        "warehouses": warehouses,
        "purchase_batches": batches,
        "policy": {
            "allow_split": allow_split,
            "minimum_first_quantity": 60,
            "last_delivery_date": day(10 if allow_split else order["due_days"]),
        },
    }


def planning_context(order_id: str) -> dict:
    from order_agent.simulator.resources import load_context

    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return load_context(conn, order_id)


def overview(context: dict) -> dict:
    return {
        "as_of": context["as_of"],
        "order": context["order"],
        "local_quantity": context["local_quantity"],
        "today": context["today"],
        "warehouses": [
            {"id": row["id"], "available_quantity": row["available_quantity"]}
            for row in context["warehouses"]
        ],
        "purchase_batches": [
            {"id": row["id"], "quantity": row["quantity"], "status": row["status"]}
            for row in context["purchase_batches"]
        ],
        "allow_split": context["policy"]["allow_split"],
    }


def investigate(order_id: str, kind: str, resource_id: str = "") -> dict:
    context = planning_context(order_id)
    return evidence_record(context, kind, resource_id)


def evidence_record(context: dict, kind: str, resource_id: str = "") -> dict:
    """生产查询和合成评测共用证据格式；查询传输仍由各自适配器负责。"""
    order_id = context["order"]["id"]
    if kind == "policy":
        data = context["policy"]
        resource_id = order_id
    else:
        rows = context["warehouses"] if kind == "route" else context["purchase_batches"]
        data = next((row for row in rows if row["id"] == resource_id), None)
        if data is None:
            raise BusinessRecordNotFound("该资源不属于当前订单的可调查范围")
    version = context["order"]["version"]
    return {
        "id": f"{kind}:{resource_id}:v{version}",
        "kind": kind,
        "resource_id": resource_id,
        "order_id": order_id,
        "order_version": version,
        "queried_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "data": data,
        "resource_digest": context["resource_digest"],
        "resource_versions": (
            {"stock": data["version"], "route": data["route_version"]}
            if kind == "route"
            else {kind: data["version"]}
        ),
    }
