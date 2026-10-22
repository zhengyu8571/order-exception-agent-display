"""PostgreSQL access for the agent service.

Every statement used to open its own connection. The transport layer already
reuses HTTP connections, so leaving the database path unpooled meant paying a
TCP connect plus SCRAM handshake for each progress event and status write.
"""

import atexit
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from order_agent.config import settings
from order_agent.domain.errors import BusinessUnavailable

# 行工厂固定为 dict_row；把行类型写进 Connection 的类型参数后，
# 仓储里的 row["column"] 才既能通过类型检查，也不会在运行期拿到元组。
Row = dict[str, Any]

# 审批等待时 task_lock 会长期持有一条会话连接，因此上限按“同时推进的任务数”
# 而不是 HTTP 并发数设置；超出时让调用方在 10 秒内失败，避免无限排队。
MAX_CONNECTIONS = 16
CHECKOUT_TIMEOUT_SECONDS = 10.0

_pools: dict[str, ConnectionPool[psycopg.Connection[Row]]] = {}
_pools_lock = threading.Lock()


def _pool(dsn: str) -> ConnectionPool[psycopg.Connection[Row]]:
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            # 不在导入时连接：脚本和测试可能从未访问数据库，也可能先改 DATABASE_URL。
            pool = ConnectionPool(
                dsn,
                min_size=0,
                max_size=MAX_CONNECTIONS,
                timeout=CHECKOUT_TIMEOUT_SECONDS,
                kwargs={"row_factory": dict_row},
                open=False,
            )
            pool.open()
            _pools[dsn] = pool
        return pool


@contextmanager
def connection(database_url: str | None = None) -> Iterator[psycopg.Connection[Row]]:
    if database_url is not None:
        # 显式 DSN 用于迁移测试和一次性库：调用方可能在同进程内 DROP 该库，
        # 池化连接会让 DROP DATABASE 失败，因此这类连接不进进程池。
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            yield conn
        return
    try:
        with _pool(settings().database_url.get_secret_value()).connection() as conn:
            yield conn
    except PoolTimeout as exc:
        # 池耗尽与数据库不可达都会走到这里；对外统一为503，不把驱动异常泄漏给调用方。
        raise BusinessUnavailable("数据库连接不可用或连接池已满，请稍后重试") from exc


def close_pools() -> None:
    """Release pooled connections; the app calls this on shutdown, atexit as fallback."""
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


atexit.register(close_pools)
