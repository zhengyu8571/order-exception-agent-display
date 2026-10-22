"""给模型提供精简的已知/未知事实；这里只做算术和投影，不替模型组合方案。"""

from order_agent.domain.scheduling import planning_start, transfer_available_on
from order_agent.integrations.investigation import TOOL_CATALOG
from order_agent.simulator.planning import overview

CONTRACT = "evidence-v2"


def check_manual(context):
    """审核无解结论的事实依据；只给反证/信息缺口，不搜索或生成分配方案。"""
    required = context["overview"]["order"]["required_quantity"]
    due = context["overview"]["order"]["due_date"]
    capacity = context["cumulative_known_capacity"]
    ready = max((row["quantity"] for row in capacity if row["date"] <= due), default=0)
    if ready >= required:
        return {
            "code": "MANUAL_CONTRADICTS_KNOWN_CAPACITY",
            "field": "action",
            "expected": f"reconsider resource combination for {required} units",
            "actual": f"verified capacity before due_date is {ready}",
        }
    unknown = sum(
        row.get("available_quantity", row.get("quantity", 0))
        for row in context["verified_resources"]
        if not row["observed"] and row.get("status", "EXPECTED") == "EXPECTED"
    )
    policy = context["customer_policy"]
    last = policy.get("last_delivery_date", due)
    ready_last = max(
        (row["quantity"] for row in capacity if row["date"] <= last), default=0
    )
    policy_unknown = policy["allow_split"] and "last_delivery_date" not in policy
    upper = (
        max((row["quantity"] for row in capacity), default=0) + unknown
        if policy_unknown
        else ready_last + unknown
    )
    if (
        context["available_queries"]
        and upper >= required
        and (unknown or policy_unknown)
    ):
        return {
            "code": "UNVERIFIED_RESOURCES_REMAIN",
            "field": "action",
            "expected": "choose relevant available_queries before concluding infeasibility",
            "actual": f"known quantity {ready_last}; unverified quantity {unknown}; policy_unknown={policy_unknown}",
        }
    return None


def query_identity(request):
    name, args = request["tool_name"], request["arguments"]
    return name, tuple(sorted(args.items()))


def evidence_query(record):
    kind, resource = record["kind"], record["resource_id"]
    return {
        "tool_name": {
            "route": "query_transfer_routes",
            "batch": "query_purchase_batch",
            "policy": "query_customer_policy",
        }[kind],
        "arguments": {"warehouse_id": resource}
        if kind == "route"
        else {"batch_id": resource}
        if kind == "batch"
        else {},
    }


def decision_context(context, state):
    summary = overview(context)
    evidence = state.get("evidence", {})
    by_resource = {(r["kind"], r["resource_id"]): r for r in evidence.values()}
    available, resources = [], []
    for row in summary["warehouses"]:
        record = by_resource.get(("route", row["id"]))
        item = {"kind": "warehouse", **row, "observed": bool(record)}
        item["maximum_allocation"] = row["available_quantity"]
        if record:
            item.update(
                evidence_id=record["id"],
                available_on=transfer_available_on(
                    context, record["data"]["transfer_days"]
                ).isoformat(),
                unit_cost_cents=record["data"]["unit_cost_cents"],
            )
        elif row["available_quantity"] > 0:
            available.append(
                {
                    "tool_name": "query_transfer_routes",
                    "arguments": {"warehouse_id": row["id"]},
                }
            )
        resources.append(item)
    for row in summary["purchase_batches"]:
        record = by_resource.get(("batch", row["id"]))
        item = {"kind": "purchase", **row, "observed": bool(record)}
        item["maximum_allocation"] = (
            row["quantity"] if row["status"] == "EXPECTED" else 0
        )
        if record:
            item.update(
                evidence_id=record["id"], available_on=record["data"]["arrival_date"]
            )
        elif row["status"] == "EXPECTED" and row["quantity"] > 0:
            available.append(
                {
                    "tool_name": "query_purchase_batch",
                    "arguments": {"batch_id": row["id"]},
                }
            )
        resources.append(item)
    policy = by_resource.get(("policy", context["order"]["id"]))
    if not policy and summary["allow_split"]:
        available.append({"tool_name": "query_customer_policy", "arguments": {}})
    previous = state.get("adaptive_proposal", {})
    releases = [(planning_start(context).isoformat(), summary["local_quantity"])]
    releases.extend(
        (row["available_on"], row.get("available_quantity", row.get("quantity", 0)))
        for row in resources
        if row["observed"] and row.get("status", "EXPECTED") == "EXPECTED"
    )
    capacity = [
        {"date": day, "quantity": sum(qty for when, qty in releases if when <= day)}
        for day in sorted({when for when, qty in releases})
    ]
    recommended = previous.get("recommended_id")
    candidate = next(
        (row for row in previous.get("candidates", []) if row["id"] == recommended),
        None,
    )
    balance = None
    if candidate:
        selected = {}
        for kind, field, identity in (
            ("warehouse", "transfers", "warehouse_id"),
            ("purchase", "purchase_allocations", "batch_id"),
        ):
            for row in candidate[field]:
                key = (kind, row[identity])
                selected[key] = selected.get(key, 0) + row["quantity"]
        allocated = candidate["local_quantity"] + sum(selected.values())
        balance = {
            "required": summary["order"]["required_quantity"],
            "allocated": allocated,
            "unallocated": summary["order"]["required_quantity"] - allocated,
            "local_used": candidate["local_quantity"],
            "local_remaining": summary["local_quantity"] - candidate["local_quantity"],
            "resources": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "used": selected.get((row["kind"], row["id"]), 0),
                    "remaining_capacity": row["maximum_allocation"]
                    - selected.get((row["kind"], row["id"]), 0),
                    "available_on": row.get("available_on", "unknown"),
                }
                for row in resources
            ],
        }
    past = []
    modes = {}
    for turn in state.get("decision_history", []):
        if turn["action"] == "propose":
            modes = {
                row["id"]: row["delivery_mode"] for row in turn.get("candidates", [])
            }
        elif turn["action"] == "validation":
            past.append(
                {
                    "modes": modes,
                    "valid_ids": turn.get("valid_ids", []),
                    "issues": [
                        {
                            key: issue[key]
                            for key in (
                                "candidate_id",
                                "code",
                                "field",
                                "expected",
                                "actual",
                            )
                            if key in issue
                        }
                        for issue in turn.get("feedback", [])
                    ][:8],
                }
            )
    # 只把推荐方案的修正问题交回模型；其他候选的错误仍留在完整审计记录中。
    feedback = [
        row
        for row in state.get("feedback", [])
        if not row.get("candidate_id") or row["candidate_id"] == recommended
    ]
    return {
        "overview": summary,
        "evidence": {
            key: {
                field: record[field] for field in ("id", "kind", "resource_id", "data")
            }
            for key, record in evidence.items()
        },
        "verified_resources": resources,
        "cumulative_known_capacity": capacity,
        "allocation_balance": balance,
        "validation_history": past[-3:],
        "local_shortfall": max(
            0, summary["order"]["required_quantity"] - summary["local_quantity"]
        ),
        "customer_policy": policy["data"]
        if policy
        else {
            "allow_split": summary["allow_split"],
            "details": "query only if SPLIT is considered",
        },
        "completed_queries": [evidence_query(record) for record in evidence.values()],
        "available_queries": available,
        "allowed_actions": ["investigate", "propose", "manual"]
        if available and state.get("investigation_rounds", 0) < 2
        else ["propose", "manual"],
        "tools": [
            tool
            for tool in TOOL_CATALOG
            if any(q["tool_name"] == tool["name"] for q in available)
        ],
        "feedback": feedback[:12],
        "previous_proposal": {
            "recommended_id": recommended,
            "candidates": [
                row
                for row in previous.get("candidates", [])
                if row["id"] == recommended
            ],
        }
        if recommended
        else None,
        "rejection_reason": (state.get("approval") or {}).get(
            "comment", state.get("rejection_reason", "")
        ),
        "remaining_investigation_rounds": max(
            0, 2 - state.get("investigation_rounds", 0)
        ),
        "remaining_proposal_attempts": max(0, 3 - state.get("proposal_attempts", 0)),
    }
