"""Real business DB changes and approval contracts; model responses are scripted."""

from copy import deepcopy
from datetime import date, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from test_adaptive_decisions import (
    after,
    candidate_for,
    context_for,
    request,
    scripted_decision,
)
from test_adaptive_integration import approval, scripted_system

from order_agent.domain.bindings import digest, resource_fingerprint
from order_agent.persistence.database import connection
from order_agent.simulator.contracts import ResourceChange


@pytest.mark.integration
def test_business_transaction_rejects_transfer_time_lost_while_waiting(live):
    order_id = live.scenario(profile="A")
    with connection() as conn:
        conn.execute(
            "UPDATE business.order_planning SET as_of=as_of-2 WHERE order_id=%s",
            (order_id,),
        )
    context = httpx.get(
        live.orders_url + f"/orders/{order_id}/planning-context",
        headers=live.service_headers,
        trust_env=False,
    ).json()
    candidate = candidate_for(context)
    body = {
        "task_id": str(uuid4()),
        "operation_id": "date-boundary-" + uuid4().hex,
        "proposal_version": 1,
        "expected_order_version": 1,
        "expected_resource_digest": context["resource_digest"],
        "approved_plan_digest": digest(candidate),
        "candidate": candidate,
    }
    url = live.orders_url + f"/orders/{order_id}/adaptive-delivery-plan"
    result = httpx.post(url, headers=live.service_headers, json=body, trust_env=False)
    assert result.status_code == 409
    assert "RESOURCE_NOT_READY" in result.text
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT version FROM business.orders WHERE id=%s", (order_id,)
            ).fetchone()["version"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                (order_id,),
            ).fetchone()["n"]
            == 0
        )
    candidate["deliveries"][0]["delivery_date"] = (
        date.fromisoformat(context["today"]) + timedelta(days=3)
    ).isoformat()
    body["approved_plan_digest"] = digest(candidate)
    result = httpx.post(url, headers=live.service_headers, json=body, trust_env=False)
    assert result.status_code == 200, result.text


def test_resource_digest_ignores_read_time_but_tracks_independent_versions():
    context = context_for("A")
    original = resource_fingerprint(context)
    context["today"] = "2099-01-01"
    context["queried_at"] = "irrelevant"
    context["warehouses"].reverse()
    assert resource_fingerprint(context) == original
    context["warehouses"][0]["route_version"] = 2
    assert resource_fingerprint(context) != original


@pytest.mark.parametrize(
    "body",
    [
        {"target": "warehouse", "resource_id": "WH-002", "allow_split": True},
        {"target": "batch", "quantity": 2},
        {"target": "policy", "date": "2026-02-30"},
        {"target": "route", "resource_id": "WH-002"},
    ],
)
def test_resource_mutation_contract_rejects_invalid_fields(body):
    with pytest.raises(ValidationError):
        ResourceChange.model_validate(body)


@pytest.mark.integration
def test_approval_binds_plan_evidence_and_resources(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)):
        task = live.task(profile="A")
        body = approval(live, task)
        for key in ("plan_digest", "evidence_digest", "resource_digest"):
            changed = deepcopy(body)
            changed[key] = "0" * 64
            live.request(
                "POST", f"/api/tasks/{task['id']}/approval", body=changed, expected=422
            )
        missing = {
            key: value for key, value in body.items() if not key.endswith("_digest")
        }
        live.request(
            "POST", f"/api/tasks/{task['id']}/approval", body=missing, expected=422
        )
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM agent.approvals WHERE task_id=%s",
                    (task["id"],),
                ).fetchone()["n"]
                == 0
            )
        done = live.request("POST", f"/api/tasks/{task['id']}/approval", body=body)
        assert done["status"] == "COMPLETED"
        with connection() as conn:
            receipt = conn.execute(
                "SELECT * FROM agent.approvals WHERE task_id=%s", (task["id"],)
            ).fetchone()
            assert all(
                receipt[key] == body[key]
                for key in ("plan_digest", "evidence_digest", "resource_digest")
            )


CHANGES = [
    {"target": "warehouse", "resource_id": "WH-003", "quantity": 39},
    {"target": "route", "resource_id": "WH-003", "unit_cost_cents": 101},
    {"target": "batch", "resource_id": "PO-003", "status": "CANCELLED"},
    {"target": "policy", "allow_split": True},
    {"target": "local", "quantity": 59},
]


@pytest.mark.integration
def test_combined_resources_and_insufficient_total_scenarios(live):
    def respond(context, turn):
        summary = context["overview"]
        total = (
            summary["local_quantity"]
            + sum(row["available_quantity"] for row in summary["warehouses"])
            + sum(row["quantity"] for row in summary["purchase_batches"])
        )
        if total < summary["order"]["required_quantity"]:
            return {"action": "manual", "reason": "受控测试：可用资源总量不足，转人工"}
        if not context["evidence"]:
            return {
                "action": "investigate",
                "requests": [
                    request("query_transfer_routes", warehouse_id="WH-002"),
                    request("query_purchase_batch", batch_id="PO-003"),
                ],
                "reason": "受控测试：核查组合资源时效",
            }
        version = summary["order"]["version"]
        return {
            "action": "propose",
            "recommended_id": "adaptive",
            "reason": "受控测试组合方案",
            "candidates": [
                {
                    "id": "adaptive",
                    "title": "调拨与采购组合",
                    "local_quantity": 60,
                    "transfers": [
                        {
                            "warehouse_id": "WH-002",
                            "quantity": 20,
                            "evidence_id": f"route:WH-002:v{version}",
                        }
                    ],
                    "purchase_allocations": [
                        {
                            "batch_id": "PO-003",
                            "quantity": 20,
                            "evidence_id": f"batch:PO-003:v{version}",
                        }
                    ],
                    "delivery_mode": "FULL",
                    "deliveries": [
                        {"quantity": 100, "delivery_date": after(summary, 3)}
                    ],
                    "rationale": "仅验证资源组合执行契约",
                    "risks": ["预计到货存在风险"],
                }
            ],
        }

    with scripted_system(live, respond):
        combined = live.task(profile="D")
        assert combined["status"] == "WAITING_APPROVAL"
        done = live.request(
            "POST",
            f"/api/tasks/{combined['id']}/approval",
            body=approval(live, combined),
        )
        assert done["status"] == "COMPLETED"
        assert done["state"]["execution"]["metrics"]["transfer_cost_cents"] == 4000
        insufficient = live.task(profile="E")
        assert insufficient["status"] == "NEEDS_MANUAL"
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                    (insufficient["order_id"],),
                ).fetchone()["n"]
                == 0
            )


@pytest.mark.integration
def test_independent_resource_changes_expire_approval_without_order_version_change(
    live,
):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)):
        for change in CHANGES:
            task = live.task(profile="F")
            assert task["status"] == "WAITING_APPROVAL"
            live.request(
                "POST", f"/api/demo/orders/{task['order_id']}/resources", body=change
            )
            with connection() as conn:
                assert (
                    conn.execute(
                        "SELECT version FROM business.orders WHERE id=%s",
                        (task["order_id"],),
                    ).fetchone()["version"]
                    == 1
                )
            done = live.request(
                "POST", f"/api/tasks/{task['id']}/approval", body=approval(live, task)
            )
            assert done["status"] == "STALE", (change, done)
            with connection() as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                        (task["order_id"],),
                    ).fetchone()["n"]
                    == 0
                )


@pytest.mark.integration
def test_business_transaction_rechecks_resources_and_replays_committed_operation(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)):
        task = live.task(profile="A")
        binding = task["approval_context"]["adaptive"]
        body = {
            "task_id": task["id"],
            "operation_id": "p3-race-" + task["id"],
            "proposal_version": task["state"]["revision"],
            "expected_order_version": 1,
            "expected_resource_digest": binding["resource_digest"],
            "approved_plan_digest": binding["plan_digest"],
            "candidate": task["state"]["adaptive_proposal"]["candidates"][0],
        }
        url = live.orders_url + f"/orders/{task['order_id']}/adaptive-delivery-plan"
        live.request(
            "POST",
            f"/api/demo/orders/{task['order_id']}/resources",
            body={"target": "route", "resource_id": "WH-003", "unit_cost_cents": 102},
        )
        result = httpx.post(
            url, headers=live.service_headers, json=body, trust_env=False
        )
        assert result.status_code == 409
        context = httpx.get(
            live.orders_url + f"/orders/{task['order_id']}/planning-context",
            headers=live.service_headers,
            trust_env=False,
        ).json()
        body["expected_resource_digest"] = context["resource_digest"]
        result = httpx.post(
            url, headers=live.service_headers, json=body, trust_env=False
        )
        assert result.status_code == 200, result.text
        assert result.json()["metrics"]["transfer_cost_cents"] == 4080
        live.request(
            "POST",
            f"/api/demo/orders/{task['order_id']}/resources",
            body={"target": "local", "quantity": 0},
        )
        replay = httpx.post(
            url, headers=live.service_headers, json=body, trust_env=False
        )
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        body["candidate"]["title"] = "changed approved content"
        assert (
            httpx.post(
                url, headers=live.service_headers, json=body, trust_env=False
            ).status_code
            == 409
        )
