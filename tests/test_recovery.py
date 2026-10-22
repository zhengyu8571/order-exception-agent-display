from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from order_agent.db import connection

pytestmark = pytest.mark.integration


def test_real_postgres_approval_survives_process_kill(live):
    task = live.task()
    assert task["status"] == "WAITING_APPROVAL"
    checkpoint_id = task["checkpoint_id"]
    original_pid = live.agent.pid
    original_instance = live.agent_instance
    original_server_pid = live.agent_server_pid
    live.restart_agent()
    assert live.agent.pid != original_pid
    assert live.agent_instance != original_instance
    assert live.agent_server_pid != original_server_pid
    restored = live.request("GET", f"/api/tasks/{task['id']}")
    assert restored["checkpoint_id"] == checkpoint_id
    assert restored["state"]["facts"] == task["state"]["facts"]
    assert restored["status"] == "WAITING_APPROVAL"
    result = live.request(
        "POST", f"/api/tasks/{task['id']}/approval", body=live.approval(task)
    )
    assert result["status"] == "COMPLETED"
    assert result["state"]["execution"]["order_version"] == 2


def test_committed_external_write_survives_agent_crash_without_duplicate(live):
    task = live.task(fault="crash_after_apply")
    try:
        live.request(
            "POST", f"/api/tasks/{task['id']}/approval", body=live.approval(task)
        )
        pytest.fail("Agent should exit before returning the execution result")
    except httpx.TransportError:
        pass
    live.agent.wait(timeout=10)
    assert live.agent.returncode == 86
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT version FROM business.orders WHERE id=%s", (task["order_id"],)
            ).fetchone()["version"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                (task["order_id"],),
            ).fetchone()["n"]
            == 1
        )
    live.restart_agent()
    restored = live.request("GET", f"/api/tasks/{task['id']}")
    assert restored["status"] == "RECOVERABLE"
    result = live.request("POST", f"/api/tasks/{task['id']}/recover")
    assert result["status"] == "COMPLETED"
    assert result["state"]["execution"]["replayed"] is True
    again = live.request(
        "POST", f"/api/tasks/{task['id']}/approval", body=live.approval(task)
    )
    assert again["status"] == "COMPLETED"
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT version FROM business.orders WHERE id=%s", (task["order_id"],)
            ).fetchone()["version"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                (task["order_id"],),
            ).fetchone()["n"]
            == 1
        )


def test_timeout_after_commit_is_reconciled(live):
    task = live.task(fault="timeout_after_commit")
    result = live.request(
        "POST", f"/api/tasks/{task['id']}/approval", body=live.approval(task)
    )
    assert result["status"] == "RECOVERABLE"
    result = live.request("POST", f"/api/tasks/{task['id']}/recover")
    assert result["status"] == "COMPLETED"
    assert result["state"]["execution"]["replayed"] is True


def test_pending_writes_reuse_successful_parallel_queries(live):
    task = live.task(fault="fail_stock_once")
    assert task["status"] == "RECOVERABLE"
    before = live.request("GET", f"/api/tasks/{task['id']}/events")
    successful = {
        r["data"]["source"] for r in before if r["type"] == "investigation.completed"
    }
    assert successful == {"order", "arrivals", "constraints"}
    live.restart_agent()
    restored = live.request("POST", f"/api/tasks/{task['id']}/recover")
    assert restored["status"] == "WAITING_APPROVAL"
    after = live.request("GET", f"/api/tasks/{task['id']}/events")
    for source in successful | {"stock"}:
        assert (
            sum(
                r["type"] == "investigation.completed"
                and r["data"].get("source") == source
                for r in after
            )
            == 1
        )


def test_stale_approval_stops_write_and_requires_new_approval(live):
    task = live.task()
    live.request("POST", f"/api/demo/orders/{task['order_id']}/change")
    result = live.request(
        "POST", f"/api/tasks/{task['id']}/approval", body=live.approval(task)
    )
    assert result["status"] == "STALE"
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                (task["order_id"],),
            ).fetchone()["n"]
            == 0
        )
    result = live.request("POST", f"/api/tasks/{task['id']}/replan")
    assert result["state"]["revision"] == 2
    assert (
        result["status"] == "BLOCKED"
    )  # 60 + 0 + 25 cannot cover 100; no invented inventory.


def test_approval_authorization_version_and_rejection(live):
    task = live.task()
    url = f"/api/tasks/{task['id']}/approval"
    live.request(
        "POST",
        url,
        body=live.approval(task),
        headers={"Authorization": "Bearer local-operator"},
        expected=403,
    )
    live.request(
        "POST", url, body=live.approval(task, proposal_version=99), expected=409
    )
    live.request(
        "POST", url, body=live.approval(task, candidate_id="split"), expected=422
    )
    rejected = live.request("POST", url, body=live.approval(task, decision="reject"))
    assert rejected["status"] == "REJECTED"
    regenerated = live.request("POST", f"/api/tasks/{task['id']}/replan")
    assert regenerated["status"] == "WAITING_APPROVAL"
    assert regenerated["state"]["revision"] == 2
    live.request("POST", url, body=live.approval(task), expected=409)


def test_concurrent_duplicate_operations_commit_once_and_reject_payload_change(live):
    task = live.task()
    body = {
        "task_id": task["id"],
        "operation_id": task["id"] + ":direct-test",
        "proposal_version": 1,
        "expected_order_version": 1,
        "candidate": task["state"]["proposal"]["candidates"][0],
    }

    def apply():
        return httpx.post(
            live.orders_url + f"/orders/{task['order_id']}/delivery-plan",
            headers=live.service_headers,
            json=body,
            trust_env=False,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: apply(), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert sum(not response.json()["replayed"] for response in responses) == 1
    body["candidate"] = {**body["candidate"], "completion_days": 4}
    assert apply().status_code == 409
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT version FROM business.orders WHERE id=%s", (task["order_id"],)
            ).fetchone()["version"]
            == 2
        )


def test_blocked_constraints_and_event_cursor(live):
    task = live.task(arrival_days=8)
    assert task["status"] == "BLOCKED"
    rows = live.request("GET", f"/api/tasks/{task['id']}/events")
    assert rows
    assert (
        live.request("GET", f"/api/tasks/{task['id']}/events?after={rows[-1]['id']}")
        == []
    )
    with httpx.stream(
        "GET",
        live.url + f"/api/tasks/{task['id']}/stream?after={rows[0]['id']}",
        headers=live.headers,
        timeout=5,
        trust_env=False,
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("id: "):
                assert int(line[4:]) > rows[0]["id"]
                break
        else:
            pytest.fail("No durable progress event emitted")
