"""Test information progress and provider deadlines, not prompt wording."""

import asyncio
import json
import time

import httpx
import pytest

from order_agent.agent.observations import decision_context
from order_agent.config import Settings
from order_agent.llm.transport import request as model_request


def test_verified_resource_projection_keeps_unknowns_and_repair_scope():
    from test_adaptive_decisions import context_for

    context = context_for("A")
    record = {
        "id": "route:WH-003:v1",
        "kind": "route",
        "resource_id": "WH-003",
        "data": context["warehouses"][1],
    }
    state = {
        "evidence": {record["id"]: record},
        "feedback": [
            {"candidate_id": "bad", "code": "WRONG_QUANTITY"},
            {"candidate_id": "other", "code": "OTHER_ERROR"},
        ],
        "adaptive_proposal": {
            "recommended_id": "bad",
            "candidates": [
                {
                    "id": "bad",
                    "local_quantity": 60,
                    "transfers": [],
                    "purchase_allocations": [],
                },
                {"id": "other"},
            ],
        },
    }
    projected = decision_context(context, state)
    assert projected["verified_resources"][1]["available_on"]
    assert "available_on" not in projected["verified_resources"][0]
    assert {
        "tool_name": "query_transfer_routes",
        "arguments": {"warehouse_id": "WH-002"},
    } in projected["available_queries"]
    assert not any(
        q["arguments"].get("warehouse_id") == "WH-003"
        or q["tool_name"] == "query_customer_policy"
        for q in projected["available_queries"]
    )
    assert [r["code"] for r in projected["feedback"]] == ["WRONG_QUANTITY"]
    assert projected["local_shortfall"] == 40
    assert projected["allocation_balance"]["unallocated"] == 40


def test_repair_balance_counts_duplicate_allocations_and_retains_past_constraints():
    from test_adaptive_decisions import context_for

    context = context_for("A")
    candidate = {
        "id": "p",
        "delivery_mode": "FULL",
        "local_quantity": 60,
        "transfers": [
            {"warehouse_id": "WH-003", "quantity": 10},
            {"warehouse_id": "WH-003", "quantity": 10},
        ],
        "purchase_allocations": [],
    }
    state = {
        "adaptive_proposal": {"recommended_id": "p", "candidates": [candidate]},
        "decision_history": [
            {"action": "propose", "candidates": [candidate]},
            {
                "action": "validation",
                "feedback": [
                    {
                        "code": "DEADLINE",
                        "field": "delivery_date",
                        "expected": "before_due",
                        "actual": "late",
                    }
                ],
            },
        ],
    }
    result = decision_context(context, state)
    assert result["allocation_balance"]["allocated"] == 80
    assert result["allocation_balance"]["unallocated"] == 20
    assert result["validation_history"][0]["issues"][0]["code"] == "DEADLINE"


@pytest.mark.integration
def test_duplicate_investigation_is_feedback_not_another_tool_round(live):
    from test_adaptive_decisions import candidate_for, request
    from test_adaptive_integration import scripted_system

    from order_agent.persistence.database import connection

    def respond(context, n):
        if n <= 2:
            return {
                "action": "investigate",
                "requests": [request("query_transfer_routes", warehouse_id="WH-003")],
                "reason": "协议测试",
            }
        assert context["feedback"][0]["code"] == "ALREADY_OBSERVED"
        assert context["remaining_investigation_rounds"] == 1
        candidate = candidate_for(context["overview"])
        return {
            "action": "propose",
            "candidates": [candidate],
            "recommended_id": candidate["id"],
            "reason": "使用已有证据",
        }

    with scripted_system(live, respond):
        task = live.task(profile="A")
        assert task["status"] == "WAITING_APPROVAL"
        assert task["state"]["investigation_rounds"] == 1
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT sum(attempts) AS n FROM agent.investigations WHERE task_id=%s",
                    (task["id"],),
                ).fetchone()["n"]
                == 1
            )


@pytest.mark.integration
def test_repeated_no_progress_terminates_and_transient_retry_is_accounted(live):
    from test_adaptive_decisions import request
    from test_adaptive_integration import scripted_system

    with scripted_system(
        live,
        lambda ctx, n: {
            "action": "investigate",
            "requests": [request("query_transfer_routes", warehouse_id="WH-003")],
            "reason": "重复查询故障替身",
        },
    ) as inputs:
        task = live.task(profile="A")
        assert task["status"] == "NEEDS_MANUAL" and len(inputs) == 3
        assert task["state"]["no_progress_rounds"] == 2
    with scripted_system(
        live,
        lambda ctx, n: (503, {"error": {"message": "temporary"}})
        if n == 1
        else {"action": "manual", "reason": "无可行方案，转人工"},
    ) as inputs:
        task = live.task(profile="E")
        assert task["status"] == "NEEDS_MANUAL" and len(inputs) == 2
        assert [row["status"] for row in task["model_calls"]] == ["FAILED", "SUCCEEDED"]
        assert task["model_calls"][0]["actual_input"] is None
        assert task["model_calls"][1]["diagnostics"]["application_retry"]


@pytest.mark.parametrize("started_body", [False, True])
def test_provider_total_deadline_cancels_header_or_stream_stall(
    monkeypatch, started_body
):
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "test",
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "{"},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
                + "\n\n"
            ).encode()
            await asyncio.sleep(5)

    async def respond(req):
        if not started_body:
            await asyncio.sleep(5)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=SlowBody()
        )

    client = httpx.AsyncClient

    class MockClient(client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    diag, usage = {}, {}
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        model_request(
            [("human", "JSON")],
            {"model": "test", "base_url": "http://test/v1", "streaming": True},
            Settings(openai_api_key="test"),
            100,
            0.5,
            diag,
            usage,
        )
    # SDK 的本地初始化不属于模拟网络等待；保留冷启动余量，仍远短于5秒故障等待。
    assert time.monotonic() - started < 3
    if "request_sent_ms" in diag:
        assert diag["elapsed_ms"] - diag["request_sent_ms"] < 1500
    assert diag["deadline_exceeded"]
    assert diag["failure_stage"] == (
        "receiving_body" if started_body else "waiting_headers"
    )
    assert not usage


@pytest.mark.integration
def test_false_manual_is_challenged_with_facts_without_supplying_a_plan(live):
    from test_adaptive_decisions import candidate_for, request
    from test_adaptive_integration import scripted_system

    def respond(context, n):
        if n == 1:
            return {
                "action": "investigate",
                "requests": [request("query_transfer_routes", warehouse_id="WH-003")],
                "reason": "查询",
            }
        if n == 2:
            return {"action": "manual", "reason": "错误算术：60+40不足100"}
        assert context["feedback"][0]["code"] == "MANUAL_CONTRADICTS_KNOWN_CAPACITY"
        assert context["previous_proposal"] is None
        candidate = candidate_for(context["overview"])
        return {
            "action": "propose",
            "candidates": [candidate],
            "recommended_id": candidate["id"],
            "reason": "依据容量重新组合",
        }

    with scripted_system(live, respond):
        task = live.task(profile="A")
        assert task["status"] == "WAITING_APPROVAL"
        assert task["state"]["manual_challenges"] == 1


@pytest.mark.integration
def test_premature_manual_requests_missing_facts_without_fixed_tool_execution(live):
    from test_adaptive_decisions import candidate_for, request
    from test_adaptive_integration import scripted_system

    def respond(context, n):
        if n == 1:
            return {"action": "manual", "reason": "忽略未调查外仓的错误结论"}
        if n == 2:
            assert context["feedback"][0]["code"] == "UNVERIFIED_RESOURCES_REMAIN"
            assert not context["evidence"]
            return {
                "action": "investigate",
                "requests": [request("query_transfer_routes", warehouse_id="WH-003")],
                "reason": "自主选择核查该仓",
            }
        candidate = candidate_for(context["overview"])
        return {
            "action": "propose",
            "candidates": [candidate],
            "recommended_id": candidate["id"],
            "reason": "组合",
        }

    with scripted_system(live, respond):
        task = live.task(profile="A")
        assert task["status"] == "WAITING_APPROVAL"
        assert task["state"]["investigation_rounds"] == 1
