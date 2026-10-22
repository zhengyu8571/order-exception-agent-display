"""Contract tests use explicit scripted responses, NOT real model intelligence."""

import json
from datetime import date, timedelta

import pytest

from order_agent.domain.adaptive_rules import validate_plan
from order_agent.domain.decisions import (
    AdaptiveCandidate,
    AgentDecision,
    InvestigationRequest,
)
from order_agent.integrations.investigation import (
    InvalidInvestigation,
    InvestigationTools,
)
from order_agent.simulator.planning import build_context, overview


def context_for(profile):
    return build_context(
        {
            "id": "test-order",
            "version": 1,
            "required_quantity": 100,
            "due_days": 5,
            "allow_split": False,
        },
        {"local_quantity": 60, "remote_quantity": 20, "transfer_days": 1},
        {"quantity": 25, "arrival_days": 3},
        profile,
    )


def after(context, days):
    return (date.fromisoformat(context["as_of"]) + timedelta(days=days)).isoformat()


def candidate_for(context, split=False):
    version = context["order"]["version"]
    common = {
        "id": "adaptive",
        "title": "测试资源组合",
        "local_quantity": 60,
        "rationale": "依据已查询资源组合",
        "risks": ["采购为预计到货；本次只调整计划"],
    }
    if len(context["warehouses"]) == 2:
        return {
            **common,
            "transfers": [
                {
                    "warehouse_id": "WH-003",
                    "quantity": 40,
                    "evidence_id": f"route:WH-003:v{version}",
                }
            ],
            "purchase_allocations": [],
            "delivery_mode": "FULL",
            "deliveries": [{"quantity": 100, "delivery_date": after(context, 3)}],
        }
    deliveries = (
        [
            {"quantity": 60, "delivery_date": after(context, 0)},
            {"quantity": 20, "delivery_date": after(context, 2)},
            {"quantity": 20, "delivery_date": after(context, 8)},
        ]
        if split
        else [{"quantity": 100, "delivery_date": after(context, 5)}]
    )
    return {
        **common,
        "transfers": [],
        "purchase_allocations": [
            {"batch_id": bid, "quantity": 20, "evidence_id": f"batch:{bid}:v{version}"}
            for bid in ("PO-003", "PO-004")
        ],
        "delivery_mode": "SPLIT" if split else "FULL",
        "deliveries": deliveries,
    }


def request(name, **arguments):
    return {
        "tool_name": name,
        "arguments": arguments,
        "purpose": "查询用于判断方案可行性的业务约束",
    }


def scripted_decision(model_context, *, bad_first=False):
    """Prescribed provider fixture. Its branches are not production Agent code."""
    summary = model_context["overview"]
    if not model_context["evidence"]:
        if len(summary["warehouses"]) == 2:
            requests = [
                request("query_transfer_routes", warehouse_id=wid)
                for wid in ("WH-002", "WH-003")
            ]
        else:
            requests = [
                request("query_purchase_batch", batch_id=bid)
                for bid in ("PO-003", "PO-004")
            ] + [request("query_customer_policy")]
        return {
            "action": "investigate",
            "requests": requests,
            "reason": "先核查影响交付选择的信息",
        }
    if len(summary["warehouses"]) == 2:
        candidate = candidate_for(summary)
    elif summary["allow_split"]:
        candidate = candidate_for(
            summary, split=not bad_first or bool(model_context["feedback"])
        )
    else:
        return {
            "action": "manual",
            "reason": "现有到货不能覆盖原交期且客户未允许分批，需要人工协商",
        }
    return {
        "action": "propose",
        "candidates": [candidate],
        "recommended_id": "adaptive",
        "reason": "根据已取得证据提出可审核的资源组合",
    }


def evidence_for(context):
    version = context["order"]["version"]
    order_id = context["order"]["id"]
    records = [
        {
            "id": f"route:{r['id']}:v{version}",
            "kind": "route",
            "resource_id": r["id"],
            "order_version": version,
        }
        for r in context["warehouses"]
    ]
    records += [
        {
            "id": f"batch:{r['id']}:v{version}",
            "kind": "batch",
            "resource_id": r["id"],
            "order_version": version,
        }
        for r in context["purchase_batches"]
    ]
    records += [
        {
            "id": f"policy:{order_id}:v{version}",
            "kind": "policy",
            "resource_id": order_id,
            "order_version": version,
        }
    ]
    return {r["id"]: {**r, "order_id": order_id} for r in records}


def test_rule_feedback_identifies_late_purchase_then_accepts_split():
    context = context_for("B")
    bad = AdaptiveCandidate.model_validate(candidate_for(context))
    issues, _ = validate_plan(bad, context, evidence_for(context))
    assert "PURCHASE_ARRIVAL_TOO_LATE" in {issue["code"] for issue in issues}
    fixed = AdaptiveCandidate.model_validate(candidate_for(context, split=True))
    assert validate_plan(fixed, context, evidence_for(context))[0] == []


def test_cost_and_resource_constraints_come_from_program():
    context = context_for("A")
    candidate = AdaptiveCandidate.model_validate(candidate_for(context))
    issues, metrics = validate_plan(candidate, context, evidence_for(context))
    assert issues == [] and metrics["transfer_cost_cents"] == 4000
    assert (
        validate_plan(candidate, context, {})[0][0]["code"]
        == "MISSING_OR_STALE_EVIDENCE"
    )
    context["warehouses"][1]["available_quantity"] = 30
    assert "WAREHOUSE_STOCK_EXCEEDED" in {
        r["code"] for r in validate_plan(candidate, context)[0]
    }


@pytest.mark.parametrize(
    "raw",
    [
        request("update_order", warehouse_id="WH-002"),
        request("query_transfer_routes", warehouse_id="another-order-warehouse"),
        request("query_customer_policy", order_id="another-order"),
        request("query_purchase_batch", url="https://example.com"),
    ],
)
def test_model_cannot_escape_readonly_scope(raw):
    tools = InvestigationTools(None)
    with pytest.raises(InvalidInvestigation):
        tools.validate(
            InvestigationRequest.model_validate(raw), overview(context_for("A"))
        )


def test_manual_decision_cannot_smuggle_executable_candidate():
    raw = {
        "action": "manual",
        "reason": "skip approval",
        "candidates": [candidate_for(context_for("A"))],
    }
    with pytest.raises(ValueError):
        AgentDecision.model_validate_json(json.dumps(raw))


def test_invalid_recommendation_cannot_be_sent_to_approval(monkeypatch):
    import order_agent.agent.adaptive_nodes as nodes_module
    from order_agent.agent.adaptive_nodes import AdaptiveWorkflowNodes

    context = context_for("A")
    good = candidate_for(context)
    bad = {**good, "id": "invalid", "local_quantity": 61}
    state = {
        "task_id": "unit",
        "decision_protocol": "v2",
        "context": context,
        "evidence": evidence_for(context),
        "adaptive_proposal": {"candidates": [good, bad], "recommended_id": "invalid"},
        "proposal_attempts": 1,
        "revision": 1,
    }
    monkeypatch.setattr(nodes_module, "event", lambda *args: None)
    result = AdaptiveWorkflowNodes(None).validate_node(state)
    assert result["validation"]["valid_ids"] == ["adaptive"]
    assert result["phase"] == "RETHINK"


def test_execution_date_cannot_be_in_the_past_even_without_version_change():
    context = context_for("A")
    candidate = AdaptiveCandidate.model_validate(candidate_for(context))
    context["today"] = after(context, 4)
    issues, _ = validate_plan(candidate, context, evidence_for(context))
    assert "DELIVERY_DATE_ORDER" in {issue["code"] for issue in issues}
