"""连接治理：进程内复用同一后端连接，且不能在归还连接后残留会话状态。

集成测试使用独立 `_test` 数据库；这里只读取后端 PID 与连接属性，不修改业务数据。
"""

import pytest

from order_agent.persistence import database
from order_agent.persistence.database import connection
from order_agent.persistence.task_repository import task_lock

pytestmark = pytest.mark.integration


def test_connection_is_reused_within_process(live):
    with connection() as conn:
        first = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    with connection() as conn:
        second = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    # 每次调用都新建连接会在每个进度事件上重复付出 TCP 与认证开销。
    assert first == second

    database.close_pools()
    with connection() as conn:
        third = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    assert third != first


def test_task_lock_restores_transactional_mode_before_returning_connection(live):
    with task_lock("pool-lock-check"):
        pass
    # autocommit 是连接状态；若不恢复，下一位使用者会拿到自动提交连接，
    # 使 SET TRANSACTION 一类的语句失败。
    with connection() as conn:
        assert conn.autocommit is False
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
