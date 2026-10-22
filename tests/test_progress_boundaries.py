"""真实 PostgreSQL 提交顺序与慢节点进度；供应商只使用明确的测试替身。"""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, local
from uuid import uuid4

import httpx
import pytest
from test_adaptive_decisions import scripted_decision
from test_adaptive_integration import scripted_system

from order_agent.persistence import task_repository
from order_agent.persistence.database import connection

pytestmark = pytest.mark.integration


def test_event_cursor_cannot_overtake_an_uncommitted_writer(live, monkeypatch):
    task_id, other_id = str(uuid4()), str(uuid4())
    for identity in (task_id, other_id):
        task_repository.create_task(identity, "event-order", "demo", "none")
    inserted, release, fast_started = Event(), Event(), Event()
    role = local()
    fast_pid = {}

    @contextmanager
    def gated_connection():
        with connection() as conn:
            if getattr(role, "kind", "") == "fast":
                fast_pid["value"] = conn.info.backend_pid
                fast_started.set()
            yield conn
            if getattr(role, "kind", "") == "slow":
                # 暂停在 INSERT 后、COMMIT 前，重现低 ID 尚不可见的窗口。
                inserted.set()
                assert release.wait(10), "test did not release pending commit"

    monkeypatch.setattr(task_repository, "connection", gated_connection)

    def write(kind):
        role.kind = kind
        task_repository.event(task_id, kind)

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(write, "slow")
        try:
            assert inserted.wait(5)
            fast = pool.submit(write, "fast")
            assert fast_started.wait(5)
            blocked = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not fast.done():
                with connection() as conn:
                    blocked = bool(
                        conn.execute(
                            "SELECT pg_blocking_pids(%s) AS blockers",
                            (fast_pid["value"],),
                        ).fetchone()["blockers"]
                    )
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked, "later event must wait before allocating its ID"
            assert task_repository.list_events(task_id, 0) == []
            # 不同任务仍可独立提交，不能用一把全局写锁修复游标。
            task_repository.event(other_id, "independent")
        finally:
            release.set()
        slow.result(timeout=5)
        fast.result(timeout=5)
    rows = task_repository.list_events(task_id, 0)
    assert [row["type"] for row in rows] == ["slow", "fast"]
    assert [
        row["type"] for row in task_repository.list_events(task_id, rows[0]["id"])
    ] == ["fast"]
    assert task_repository.list_events(task_id, rows[-1]["id"]) == []


def test_deferred_task_survives_restart_and_streams_before_slow_run_finishes(live):
    entered, release = Event(), Event()

    def respond(context, turn):
        if turn == 1:
            entered.set()
            assert release.wait(15), "test did not release scripted provider"
        return scripted_decision(context)

    with scripted_system(live, respond) as inputs:
        order_id = live.scenario(profile="A")
        task = live.request(
            "POST", "/api/tasks?defer_start=true", body={"order_id": order_id}
        )
        assert task["status"] == "CREATED" and task["checkpoint_id"] is None
        assert inputs == [] and task["state"] == {}
        created_events = live.request("GET", f"/api/tasks/{task['id']}/events")
        live.restart_agent()
        restored = live.request("GET", f"/api/tasks/{task['id']}")
        assert restored["status"] == "CREATED" and not restored["active"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            run = pool.submit(live.request, "POST", f"/api/tasks/{task['id']}/recover")
            try:
                assert entered.wait(10)
                assert not run.done()
                active = live.request("GET", f"/api/tasks/{task['id']}")
                assert active["active"] and active["status"] == "RUNNING"
                # 真正从 SSE 读取：不是在执行结束后检查积累下来的日志。
                url = (
                    live.url
                    + f"/api/tasks/{task['id']}/stream?after={created_events[-1]['id']}"
                )
                with httpx.stream(
                    "GET", url, headers=live.headers, trust_env=False, timeout=5
                ) as stream:
                    assert stream.status_code == 200
                    for line in stream.iter_lines():
                        if line.startswith("data:") and '"stage": "propose"' in line:
                            assert '"type": "graph.stage.started"' in line
                            assert not run.done()
                            break
                    else:
                        pytest.fail("no live proposal stage event")
            finally:
                release.set()
            assert run.result(timeout=30)["status"] == "WAITING_APPROVAL"
