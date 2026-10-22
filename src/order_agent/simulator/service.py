"""Business transactions: deliberately independent of graph checkpoint transactions."""

import hashlib
import json
import time
from uuid import uuid4

from psycopg.types.json import Jsonb

from order_agent.config import settings
from order_agent.domain.adaptive_rules import validate_plan
from order_agent.domain.bindings import digest as plan_digest
from order_agent.domain.bindings import resource_fingerprint
from order_agent.domain.contracts import ApplyPlan
from order_agent.domain.decisions import AdaptiveApply
from order_agent.domain.errors import (
    BusinessConflict,
    BusinessRecordNotFound,
    FaultsDisabled,
)
from order_agent.domain.rules import validate_candidate
from order_agent.persistence.database import connection
from order_agent.simulator.contracts import BusinessChange, Scenario


def seed_scenario(scenario: Scenario) -> str:
    order_id = scenario.id or "SO-" + uuid4().hex[:12].upper()
    with connection() as conn:
        inserted = conn.execute(
            "INSERT INTO business.orders(id,customer,required_quantity,due_days,allow_split) "
            "VALUES(%s,%s,100,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
            (order_id, "华东精密设备有限公司", scenario.due_days, scenario.allow_split),
        ).fetchone()
        if inserted:
            conn.execute("INSERT INTO business.stock VALUES(%s,60,20,1)", (order_id,))
            conn.execute(
                "INSERT INTO business.arrivals VALUES(%s,25,%s)",
                (order_id, scenario.arrival_days),
            )
            from order_agent.simulator.planning import build_context

            order = row_or_404(
                conn, "SELECT * FROM business.orders WHERE id=%s", order_id
            )
            context = build_context(
                order,
                {"local_quantity": 60, "remote_quantity": 20, "transfer_days": 1},
                {"quantity": 25, "arrival_days": scenario.arrival_days},
                scenario.profile,
            )
            conn.execute(
                "UPDATE business.orders SET planning_context=%s WHERE id=%s",
                (Jsonb(context), order_id),
            )
            from order_agent.simulator.resources import seed_resources

            seed_resources(conn, order_id, context)
    return order_id


def row_or_404(conn, query: str, order_id: str) -> dict:
    row = conn.execute(query, (order_id,)).fetchone()
    if not row:
        raise BusinessRecordNotFound("订单或业务记录不存在")
    return row


def list_orders():
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM business.orders ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()


def query_order(order_id: str):
    with connection() as conn:
        return row_or_404(conn, "SELECT * FROM business.orders WHERE id=%s", order_id)


def query_stock(order_id: str):
    with connection() as conn:
        return row_or_404(
            conn, "SELECT * FROM business.stock WHERE order_id=%s", order_id
        )


def query_arrivals(order_id: str):
    with connection() as conn:
        return row_or_404(
            conn, "SELECT * FROM business.arrivals WHERE order_id=%s", order_id
        )


def query_constraints(order_id: str):
    order = query_order(order_id)
    return {
        "due_days": order["due_days"],
        "allow_split": order["allow_split"],
        "order_version": order["version"],
    }


def query_operation(operation_id: str):
    with connection() as conn:
        return row_or_404(
            conn,
            "SELECT * FROM business.operations WHERE operation_id=%s",
            operation_id,
        )


def apply_plan(order_id: str, body: ApplyPlan | AdaptiveApply):
    if body.fault != "none" and not settings().enable_faults:
        raise FaultsDisabled("故障注入未开启")
    # 故障开关模拟传输异常，不是业务动作的一部分；恢复后去掉故障开关仍是同一操作。
    payload = {
        "order_id": order_id,
        **body.model_dump(exclude={"fault"}, exclude_none=True),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    cached = False
    with connection() as conn:
        # 先按 operation_id 串行化，再查幂等账本，防止并发重试都判断为“尚未执行”。
        # PostgreSQL 哈希碰撞最多导致额外等待，不会把不同操作当成同一个。
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (body.operation_id,)
        )
        previous = conn.execute(
            "SELECT * FROM business.operations WHERE operation_id=%s",
            (body.operation_id,),
        ).fetchone()
        if previous:
            # 必须先查账本再查最新版本：第一次写入本身已增加版本，重放不能因此被误拒绝。
            if previous["payload_hash"] != digest:
                raise BusinessConflict("同一 operation_id 不允许更换业务参数")
            result = previous["result"]
            cached = True
        else:
            order = row_or_404(
                conn, "SELECT * FROM business.orders WHERE id=%s FOR UPDATE", order_id
            )
            stock = row_or_404(
                conn,
                "SELECT * FROM business.stock WHERE order_id=%s FOR UPDATE",
                order_id,
            )
            arrivals = row_or_404(
                conn,
                "SELECT * FROM business.arrivals WHERE order_id=%s FOR UPDATE",
                order_id,
            )
            if order["version"] != body.expected_order_version:
                raise BusinessConflict("订单版本已变化，需要重新调查和审批")
            metrics = None
            if isinstance(body, AdaptiveApply):
                from order_agent.simulator.resources import load_context

                # API 层的复核到真正写入之间仍可能发生变更，业务事务必须再次锁定并校验。
                context = load_context(conn, order_id, lock=True)
                if body.approved_plan_digest != plan_digest(
                    body.candidate.model_dump()
                ):
                    raise BusinessConflict("执行动作缺少有效审批摘要或与批准内容不同")
                if body.expected_resource_digest != resource_fingerprint(context):
                    raise BusinessConflict(
                        "库存、路线、采购或客户政策已变化，必须重新审批"
                    )
                errors, metrics = validate_plan(body.candidate, context)
            else:
                errors = validate_candidate(
                    body.candidate,
                    {"order": order, "stock": stock, "arrivals": arrivals},
                )
            if errors:
                raise BusinessConflict(
                    {"message": "执行前业务校验失败", "errors": errors}
                )
            updated = conn.execute(
                "UPDATE business.orders SET delivery_plan=%s,version=version+1,status='PLAN_APPLIED',updated_at=now() "
                "WHERE id=%s AND version=%s RETURNING version",
                (
                    Jsonb(body.candidate.model_dump()),
                    order_id,
                    body.expected_order_version,
                ),
            ).fetchone()
            if not updated:
                raise BusinessConflict("订单发生并发修改")
            result = {
                "operation_id": body.operation_id,
                "order_id": order_id,
                "proposal_version": body.proposal_version,
                "order_version": updated["version"],
                "status": "APPLIED",
                "plan": body.candidate.model_dump(),
            }
            if isinstance(body, AdaptiveApply) and metrics is not None:
                result["metrics"] = metrics
                result["plan_digest"] = body.approved_plan_digest
                result["resource_digest"] = body.expected_resource_digest
            conn.execute(
                "INSERT INTO business.operations(operation_id,order_id,payload_hash,result) VALUES(%s,%s,%s,%s)",
                (body.operation_id, order_id, digest, Jsonb(result)),
            )
    # 离开 connection 上下文才提交：订单修改和成功账本同成同败。
    # 故意在 COMMIT 后超时，复现“业务成功、Agent 没收到结果”的不确定窗口。
    if body.fault == "timeout_after_commit" and not cached:
        time.sleep(1.5)
    return {**result, "replayed": cached}


def create_scenario(body: Scenario):
    return {"order_id": seed_scenario(body)}


def change_order(order_id: str, body: BusinessChange):
    """Simulate an independent ERP change while a proposal waits for approval."""
    with connection() as conn:
        order = row_or_404(
            conn, "SELECT * FROM business.orders WHERE id=%s FOR UPDATE", order_id
        )
        conn.execute(
            "UPDATE business.stock SET remote_quantity=%s WHERE order_id=%s",
            (body.remote_quantity, order_id),
        )
        conn.execute(
            "UPDATE business.orders SET version=version+1,updated_at=now() WHERE id=%s",
            (order_id,),
        )
        if order["planning_context"]:
            context = order["planning_context"]
            for row in context["warehouses"]:
                row["available_quantity"] = body.remote_quantity
            conn.execute(
                "UPDATE business.orders SET planning_context=%s WHERE id=%s",
                (Jsonb(context), order_id),
            )
        conn.execute(
            "UPDATE business.warehouse_stock SET available_quantity=%s,version=version+1 WHERE order_id=%s",
            (body.remote_quantity, order_id),
        )
    return {"changed": True}
