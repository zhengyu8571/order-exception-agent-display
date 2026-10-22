"""独立确定性参照：有限整数资源枚举，不使用模型或语料标签。"""

from datetime import date, timedelta
from itertools import product

from order_agent.domain.decisions import AdaptiveCandidate
from order_agent.domain.scheduling import planning_start, transfer_available_on


def resources(context):
    anchor = planning_start(context)
    return [
        {
            "kind": "local",
            "id": "local",
            "quantity": context["local_quantity"],
            "ready": anchor,
            "cost": 0,
        },
        *[
            {
                "kind": "route",
                "id": row["id"],
                "quantity": row["available_quantity"],
                "ready": transfer_available_on(context, row["transfer_days"]),
                "cost": row["unit_cost_cents"],
            }
            for row in context["warehouses"]
        ],
        *[
            {
                "kind": "batch",
                "id": row["id"],
                "quantity": row["quantity"],
                "ready": date.fromisoformat(row["arrival_date"]),
                "cost": 0,
            }
            for row in context["purchase_batches"]
            if row["status"] == "EXPECTED"
        ],
    ]


def make_plan(context, allocations, mode):
    required = context["order"]["required_quantity"]
    selected = [(row, quantity) for row, quantity in allocations if quantity]
    if sum(quantity for _, quantity in selected) != required:
        return None
    today = date.fromisoformat(context["today"])
    due = date.fromisoformat(context["order"]["due_date"])
    finish = max(today, *(row["ready"] for row, _ in selected))
    if mode == "FULL":
        if finish > due:
            return None
        deliveries = [{"quantity": required, "delivery_date": finish.isoformat()}]
    else:
        policy = context["policy"]
        if not policy["allow_split"] or required <= max(
            1, policy["minimum_first_quantity"]
        ):
            return None
        threshold = max(1, policy["minimum_first_quantity"])
        dates = sorted({max(today, row["ready"]) for row, _ in selected})
        first = next(
            (
                when
                for when in dates
                if sum(q for row, q in selected if row["ready"] <= when) >= threshold
            ),
            None,
        )
        if first is None or first > due:
            return None
        first_quantity = min(
            required - 1, sum(q for row, q in selected if row["ready"] <= first)
        )
        finish = max(finish, first + timedelta(days=1))
        if finish > date.fromisoformat(policy["last_delivery_date"]):
            return None
        deliveries = [
            {"quantity": first_quantity, "delivery_date": first.isoformat()},
            {
                "quantity": required - first_quantity,
                "delivery_date": finish.isoformat(),
            },
        ]
    version = context["order"]["version"]
    return AdaptiveCandidate(
        id="deterministic",
        title="确定性资源方案",
        local_quantity=sum(q for row, q in selected if row["kind"] == "local"),
        transfers=[
            {
                "warehouse_id": row["id"],
                "quantity": q,
                "evidence_id": f"route:{row['id']}:v{version}",
            }
            for row, q in selected
            if row["kind"] == "route"
        ],
        purchase_allocations=[
            {
                "batch_id": row["id"],
                "quantity": q,
                "evidence_id": f"batch:{row['id']}:v{version}",
            }
            for row, q in selected
            if row["kind"] == "batch"
        ],
        delivery_mode=mode,
        deliveries=deliveries,
        rationale="基于可用数量和可用日期的确定性计算",
        risks=["仅比较计划；采购为预计到货，不代表实物履约"],
    )


def greedy_plan(context):
    # 基线优先整单；资源按可用时间、单价排序。它会覆盖所有资源，不能只看第一仓和第一批。
    for mode in ("FULL", "SPLIT"):
        remaining, allocated = context["order"]["required_quantity"], []
        deadline = date.fromisoformat(
            context["order"]["due_date"]
            if mode == "FULL"
            else context["policy"]["last_delivery_date"]
        )
        for row in sorted(
            resources(context), key=lambda r: (r["ready"], r["cost"], r["id"])
        ):
            if row["ready"] > deadline:
                continue
            quantity = min(remaining, row["quantity"])
            allocated.append((row, quantity))
            remaining -= quantity
        plan = make_plan(context, allocated, mode)
        if plan:
            return plan
    return None


def oracle(context):
    # 本语料最多两个外仓。枚举每个仓库的整数取用量，免费资源按最早可用填充剩余需求。
    # 只对当前模型给出模式内最低调拨费，不宣称是通用供应链优化器或全部履约成本最优。
    all_resources = resources(context)
    warehouses = [row for row in all_resources if row["kind"] == "route"]
    if len(warehouses) > 2 or context["order"]["required_quantity"] > 200:
        raise ValueError("穷举参照只支持 <=2 个外仓、需求 <=200 的评测域")
    required = context["order"]["required_quantity"]
    free = sorted(
        [row for row in all_resources if row["kind"] != "route"],
        key=lambda row: (row["ready"], row["id"]),
    )
    best = {"FULL": None, "SPLIT": None}
    for mode in best:
        if mode == "SPLIT" and not context["policy"]["allow_split"]:
            continue
        deadline = date.fromisoformat(
            context["order"]["due_date"]
            if mode == "FULL"
            else context["policy"]["last_delivery_date"]
        )
        quantities = [
            range(min(required, row["quantity"]) + 1)
            if row["ready"] <= deadline
            else range(1)
            for row in warehouses
        ]
        for combination in product(*quantities):
            if sum(combination) > required:
                continue
            cost = sum(row["cost"] * q for row, q in zip(warehouses, combination))
            if best[mode] and cost > best[mode]["cost_cents"]:
                continue
            remaining = required - sum(combination)
            allocated = list(zip(warehouses, combination))
            for row in free:
                quantity = (
                    min(remaining, row["quantity"]) if row["ready"] <= deadline else 0
                )
                allocated.append((row, quantity))
                remaining -= quantity
            plan = make_plan(context, allocated, mode)
            if plan is None:
                continue
            key = (cost, plan.deliveries[-1].delivery_date)
            if best[mode] is None or key < (
                best[mode]["cost_cents"],
                best[mode]["completion_date"],
            ):
                best[mode] = {
                    "cost_cents": cost,
                    "completion_date": plan.deliveries[-1].delivery_date,
                    "plan": plan.model_dump(),
                }
    return {"feasible": any(best.values()), "modes": best}
