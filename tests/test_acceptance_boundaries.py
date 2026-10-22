"""最终可靠性边界：真实数据库、进程退出和并发请求，模型响应仍明确为替身。"""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from test_adaptive_decisions import scripted_decision
from test_adaptive_integration import approval, scripted_system

from order_agent.persistence.database import connection

pytestmark = pytest.mark.integration


def count_operations(order_id):
    with connection() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
            (order_id,),
        ).fetchone()["n"]


def test_approval_receipt_survives_exit_before_graph_resume(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)) as inputs:
        task = live.task(profile="A", fault="crash_after_approval")
        with pytest.raises(httpx.TransportError):
            live.request(
                "POST", f"/api/tasks/{task['id']}/approval", body=approval(live, task)
            )
        live.agent.wait(timeout=10)
        assert live.agent.returncode == 87
        assert count_operations(task["order_id"]) == 0
        with connection() as conn:
            receipt = conn.execute(
                "SELECT decision FROM agent.approvals WHERE task_id=%s", (task["id"],)
            ).fetchone()["decision"]
            assert (
                receipt["plan_digest"]
                == task["approval_context"]["adaptive"]["plan_digest"]
            )
        live.restart_agent()
        restored = live.request("GET", f"/api/tasks/{task['id']}")
        assert (
            restored["status"] == "WAITING_APPROVAL"
            and restored["checkpoint_id"] == task["checkpoint_id"]
        )
        done = live.request("POST", f"/api/tasks/{task['id']}/recover")
        assert done["status"] == "COMPLETED" and len(inputs) == 2
        assert count_operations(task["order_id"]) == 1


def test_adaptive_timeout_reconciles_same_operation_without_more_model_calls(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)) as inputs:
        task = live.task(profile="A", fault="timeout_after_commit")
        uncertain = live.request(
            "POST", f"/api/tasks/{task['id']}/approval", body=approval(live, task)
        )
        assert uncertain["status"] == "RECOVERABLE"
        assert count_operations(task["order_id"]) == 1
        live.restart_agent()
        done = live.request("POST", f"/api/tasks/{task['id']}/recover")
        assert done["status"] == "COMPLETED" and done["state"]["execution"]["replayed"]
        assert len(inputs) == 2 and count_operations(task["order_id"]) == 1


def test_concurrent_approvals_serialize_and_replay_without_duplicate_write(live):
    with scripted_system(live, lambda ctx, n: scripted_decision(ctx)):
        task = live.task(profile="A")
        body = approval(live, task)
        url = live.url + f"/api/tasks/{task['id']}/approval"

        def approve_once():
            return httpx.post(
                url, headers=live.headers, json=body, trust_env=False, timeout=40
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda _: approve_once(), range(3)))
        assert all(response.status_code in {200, 409} for response in responses)
        assert any(response.status_code == 200 for response in responses)
        done = live.request("POST", f"/api/tasks/{task['id']}/approval", body=body)
        assert done["status"] == "COMPLETED" and count_operations(task["order_id"]) == 1
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM agent.approvals WHERE task_id=%s",
                    (task["id"],),
                ).fetchone()["n"]
                == 1
            )
