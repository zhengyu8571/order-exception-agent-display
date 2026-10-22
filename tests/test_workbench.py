"""工作台分页与访问契约；真实 API 和独立测试数据库。"""

from uuid import uuid4

import pytest

from order_agent.persistence.database import connection

pytestmark = pytest.mark.integration


def test_task_page_search_filter_counts_and_bounds(live):
    prefix = "PAGE-" + uuid4().hex
    with connection() as conn:
        for index, status in enumerate(
            ["WAITING_APPROVAL", "WAITING_APPROVAL", "COMPLETED", "RUNNING"]
        ):
            conn.execute(
                "INSERT INTO agent.tasks(id,order_id,status,planner_mode) VALUES(%s,%s,%s,'demo')",
                (f"{prefix}-{index}", prefix, status),
            )
    first = live.request("GET", f"/api/tasks/page?search={prefix}&limit=2")
    second = live.request("GET", f"/api/tasks/page?search={prefix}&limit=2&offset=2")
    assert first["total"] == second["total"] == 4
    assert len({row["id"] for page in (first, second) for row in page["items"]}) == 4
    assert first["counts"]["WAITING_APPROVAL"] >= 2
    waiting = live.request(
        "GET", f"/api/tasks/page?search={prefix}&status=WAITING_APPROVAL"
    )
    assert waiting["total"] == 2
    attention = live.request("GET", f"/api/tasks/page?search={prefix}&status=attention")
    assert attention["total"] == 1 and attention["items"][0]["status"] == "RECOVERABLE"
    # 搜索内容中的百分号是普通字符，不允许扩大成任意匹配。
    literal = live.request("GET", f"/api/tasks/page?search={prefix}%25")
    assert literal["total"] == 0
    live.request("GET", "/api/tasks/page?limit=1000", expected=422)
    live.request("GET", "/api/tasks/page?status=arbitrary", expected=422)


def test_operator_cannot_change_resources_and_malformed_change_is_rejected(live):
    order_id = live.scenario(profile="A")
    path = f"/api/demo/orders/{order_id}/resources"
    live.request(
        "POST",
        path,
        body={"target": "local", "quantity": 1},
        headers={"Authorization": "Bearer local-operator"},
        expected=403,
    )
    live.request(
        "POST",
        path,
        body={"target": "route", "resource_id": "WH-003", "allow_split": True},
        expected=422,
    )
