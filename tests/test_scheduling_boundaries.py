"""跨天时效同时约束业务校验、模型观察与固定规则参照，不调用模型。"""

from copy import deepcopy
from datetime import date

from test_adaptive_decisions import after, candidate_for, context_for

from order_agent.agent.observations import decision_context
from order_agent.domain.adaptive_rules import validate_plan
from order_agent.domain.decisions import AdaptiveCandidate
from order_agent.evaluation.strategies import greedy_plan, oracle


def test_approval_wait_does_not_count_as_transfer_time():
    context = context_for("A")
    raw = candidate_for(context)
    assert validate_plan(AdaptiveCandidate.model_validate(raw), context)[0] == []
    original_due = context["order"]["due_date"]
    original_purchase = deepcopy(context["purchase_batches"])
    context["today"] = after(context, 2)
    issues, _ = validate_plan(AdaptiveCandidate.model_validate(raw), context)
    assert "RESOURCE_NOT_READY" in {row["code"] for row in issues}
    raw["deliveries"][0]["delivery_date"] = after(context, 5)
    assert validate_plan(AdaptiveCandidate.model_validate(raw), context)[0] == []
    assert context["order"]["due_date"] == original_due
    assert context["purchase_batches"] == original_purchase


def test_observations_and_rule_references_recompute_transfer_readiness():
    context = context_for("A")
    context["today"] = after(context, 3)
    records = {
        f"route:{row['id']}:v1": {
            "id": f"route:{row['id']}:v1",
            "kind": "route",
            "resource_id": row["id"],
            "data": row,
        }
        for row in context["warehouses"]
    }
    view = decision_context(context, {"evidence": records})
    warehouse = next(row for row in view["verified_resources"] if row["id"] == "WH-003")
    assert warehouse["available_on"] == after(context, 6)
    assert all(
        row["date"] >= context["today"] for row in view["cumulative_known_capacity"]
    )
    # 便宜仓已赶不上原交期，其他资源数量不足；基线和有界穷举也不能沿用旧可用日。
    assert greedy_plan(context) is None
    assert oracle(context)["feasible"] is False
    assert date.fromisoformat(context["order"]["due_date"]) < date.fromisoformat(
        warehouse["available_on"]
    )
