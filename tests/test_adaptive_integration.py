"""Real HTTP, Postgres and processes, but a SCRIPTED OpenAI-compatible provider.

These tests prove runtime contracts. They do not count as live LLM quality evals.
"""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from test_adaptive_decisions import candidate_for, request, scripted_decision

from order_agent.db import connection

pytestmark = pytest.mark.integration


@contextmanager
def scripted_system(live, respond, **options):
    inputs = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            context = json.loads(payload["messages"][-1]["content"])
            inputs.append(context)
            answer = respond(context, len(inputs))
            if isinstance(answer, tuple):
                status, error = answer
                body = json.dumps(error).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            content = (
                answer
                if isinstance(answer, str)
                else json.dumps(answer, ensure_ascii=False)
            )
            result = {
                "id": "scripted-call",
                "object": "chat.completion",
                "created": 1,
                "model": "scripted-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                },
            }
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class ProviderServer(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

    server = ProviderServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    previous = dict(live.env)
    live.env.update(
        PLANNER_MODE="llm",
        LLM_PROVIDER="scripted-test",
        LLM_MODEL="scripted-model",
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
        LLM_TRUST_ENV="false",
        **options,
    )
    try:
        live.restart_agent()
        yield inputs
    finally:
        live.env = previous
        live.restart_agent()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def approval(live, task):
    return live.approval(task, candidate_id="adaptive")


def test_two_investigations_fan_out_and_merge_once(live):
    """一轮里请求两个调查：Send 扇出两个分支，汇合节点只递增一次轮数。"""

    def respond(context, n):
        if n == 1:
            return {
                "action": "investigate",
                "requests": [
                    request("query_transfer_routes", warehouse_id="WH-002"),
                    request("query_transfer_routes", warehouse_id="WH-003"),
                ],
                "reason": "一次比较两个外仓的路线",
            }
        assert context["remaining_investigation_rounds"] == 1
        candidate = candidate_for(context["overview"])
        return {
            "action": "propose",
            "candidates": [candidate],
            "recommended_id": candidate["id"],
            "reason": "使用两条路线证据",
        }

    with scripted_system(live, respond):
        task = live.task(profile="A")
        assert task["status"] == "WAITING_APPROVAL"
        assert task["state"]["investigation_rounds"] == 1
        events = live.request("GET", f"/api/tasks/{task['id']}/events")
        supplemented = [
            row for row in events if row["type"] == "investigation.supplemental"
        ]
        assert {row["data"]["arguments"]["warehouse_id"] for row in supplemented} == {
            "WH-002",
            "WH-003",
        }
        assert {row["data"]["evidence_id"] for row in supplemented} == {
            "route:WH-002:v1",
            "route:WH-003:v1",
        }
        started = [
            row
            for row in events
            if row["type"] == "graph.stage.started"
            and row["data"]["stage"] == "supplemental"
        ]
        assert len(started) == 2
        propose_done = [
            row
            for row in events
            if row["type"] == "graph.stage.finished"
            and row["data"]["stage"] == "propose"
        ]
        assert propose_done and propose_done[0]["data"]["duration_ms"] is not None


def test_scripted_provider_different_evidence_and_outcomes_for_abc(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)) as inputs:
        tasks = []
        for profile in ("A", "B", "C"):
            task = live.task(profile=profile)
            tasks.append(task)
        assert [task["status"] for task in tasks] == [
            "WAITING_APPROVAL",
            "WAITING_APPROVAL",
            "NEEDS_MANUAL",
        ]
        traces = [
            [
                r["requests"]
                for r in task["state"]["decision_history"]
                if r["action"] == "investigate"
            ]
            for task in tasks
        ]
        assert {r["tool_name"] for r in traces[0][0]} == {"query_transfer_routes"}
        assert {r["tool_name"] for r in traces[1][0]} == {
            "query_purchase_batch",
            "query_customer_policy",
        }
        assert (
            tasks[0]["state"]["adaptive_proposal"]["candidates"][0]["delivery_mode"]
            == "FULL"
        )
        assert (
            tasks[1]["state"]["adaptive_proposal"]["candidates"][0]["delivery_mode"]
            == "SPLIT"
        )
        assert len(inputs) == 6
        for task in tasks[:2]:
            done = live.request(
                "POST", f"/api/tasks/{task['id']}/approval", body=approval(live, task)
            )
            assert done["status"] == "COMPLETED"
            assert all(
                row["provider"] == "scripted-test" for row in done["model_calls"]
            )
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                    (tasks[2]["order_id"],),
                ).fetchone()["n"]
                == 0
            )


def test_invalid_plan_feedback_changes_next_model_input_and_plan(live):
    with scripted_system(
        live, lambda ctx, n: scripted_decision(ctx, bad_first=True)
    ) as inputs:
        task = live.task(profile="B")
        assert task["status"] == "WAITING_APPROVAL"
        assert task["state"]["proposal_attempts"] == 2
        assert "PURCHASE_ARRIVAL_TOO_LATE" in {r["code"] for r in inputs[2]["feedback"]}
        assert task["state"]["revision"] == 2
        assert task["state"]["validation"]["issues"]["adaptive"] == []


def test_unknown_tool_and_malformed_json_reenter_decision_safely(live):
    def respond(context, n):
        if n == 1:
            return "not valid JSON"
        if n == 2:
            assert context["feedback"][0]["code"] == "INVALID_MODEL_OUTPUT"
            return {
                "action": "investigate",
                "requests": [request("update_order", order_id="victim")],
                "reason": "attempt a forbidden write",
            }
        assert context["feedback"][0]["code"] == "INVALID_TOOL_REQUEST"
        return {"action": "manual", "reason": "无法在授权范围内完成，转人工"}

    with scripted_system(live, respond) as inputs:
        task = live.task(profile="E")
        assert task["status"] == "NEEDS_MANUAL" and len(inputs) == 3
        assert [r["status"] for r in task["model_calls"]] == [
            "INVALID_OUTPUT",
            "SUCCEEDED",
            "SUCCEEDED",
        ]
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                    (task["order_id"],),
                ).fetchone()["n"]
                == 0
            )


def test_root_action_contract_feedback_allows_model_to_correct(live):
    def respond(context, n):
        if n == 1:
            return {
                "action": "manual",
                "requests": [request("query_customer_policy")],
                "reason": "错误地把人工结论和调查请求混在一个动作中",
            }
        issue = context["feedback"][0]
        assert issue["code"] == "INVALID_MODEL_OUTPUT"
        constraint = issue["constraints"][0]
        assert constraint["field"] == "$"
        assert "manual cannot contain" in constraint["constraint"]
        assert "input" not in constraint
        return {"action": "manual", "reason": "已按动作互斥契约修正，转人工协商"}

    with scripted_system(live, respond) as inputs:
        task = live.task(profile="E")
        assert task["status"] == "NEEDS_MANUAL" and len(inputs) == 2
        assert [row["status"] for row in task["model_calls"]] == [
            "INVALID_OUTPUT",
            "SUCCEEDED",
        ]
        assert task["state"]["decision_history"][-1]["action"] == "manual"


def test_task_model_budget_stops_further_provider_calls(live):
    with scripted_system(
        live, lambda ctx, n: scripted_decision(ctx), MODEL_MAX_CALLS="1"
    ) as inputs:
        task = live.task(profile="A")
        assert task["status"] == "NEEDS_MANUAL"
        assert len(inputs) == 1 and len(task["model_calls"]) == 1
        assert "预算" in task["state"]["error"]


def test_provider_failure_holds_budget_and_does_not_fallback(live):
    with scripted_system(
        live,
        lambda ctx, n: (
            503,
            {"error": {"message": "test unavailable", "type": "server_error"}},
        ),
        MODEL_MAX_CALLS="1",
    ) as inputs:
        task = live.task(profile="A")
        assert task["status"] == "RECOVERABLE" and len(inputs) == 1
        assert task["model_calls"][0]["status"] == "FAILED"
        assert task["model_calls"][0]["actual_input"] is None
        assert task["model_calls"][0]["reserved_input"] > 0
        restored = live.request("POST", f"/api/tasks/{task['id']}/recover")
        assert restored["status"] == "NEEDS_MANUAL" and len(inputs) == 1
        assert not restored["state"].get("proposal")


def test_model_result_replayed_after_process_exit_without_second_call(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)) as inputs:
        order_id = live.scenario(profile="A")
        try:
            live.request(
                "POST",
                "/api/tasks",
                body={"order_id": order_id, "fault": "crash_after_decision"},
            )
            pytest.fail("expected real Agent exit")
        except httpx.TransportError:
            pass
        live.agent.wait(timeout=10)
        assert live.agent.returncode == 86 and len(inputs) == 1
        with connection() as conn:
            task_id = conn.execute(
                "SELECT id FROM agent.tasks WHERE order_id=%s", (order_id,)
            ).fetchone()["id"]
        live.restart_agent()
        task = live.request("POST", f"/api/tasks/{task_id}/recover")
        assert task["status"] == "WAITING_APPROVAL" and len(inputs) == 2
        assert len(task["model_calls"]) == 2
        assert task["state"]["decision_history"][0]["replayed"] is True
        original_count = len(inputs)
        live.restart_agent()
        task = live.request("GET", f"/api/tasks/{task_id}")
        done = live.request(
            "POST", f"/api/tasks/{task_id}/approval", body=approval(live, task)
        )
        assert done["status"] == "COMPLETED" and len(inputs) == original_count


def test_adaptive_write_commit_crash_reconciles_without_regeneration(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)) as inputs:
        task = live.task(profile="A", fault="crash_after_apply")
        try:
            live.request(
                "POST", f"/api/tasks/{task['id']}/approval", body=approval(live, task)
            )
            pytest.fail("expected actual exit after business commit")
        except httpx.TransportError:
            pass
        live.agent.wait(timeout=10)
        assert live.agent.returncode == 86
        live.restart_agent()
        done = live.request("POST", f"/api/tasks/{task['id']}/recover")
        assert done["status"] == "COMPLETED" and done["state"]["execution"]["replayed"]
        assert len(inputs) == 2
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                    (task["order_id"],),
                ).fetchone()["n"]
                == 1
            )
