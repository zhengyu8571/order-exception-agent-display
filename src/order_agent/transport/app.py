"""Agent HTTP assembly: lifecycle, dependencies and route registration."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import psycopg
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from order_agent.agent.graph import build_graph
from order_agent.config import settings
from order_agent.llm.pool import provider_pool
from order_agent.persistence.database import close_pools, connection
from order_agent.persistence.migrations import apply_migrations
from order_agent.services.tasks import TaskService
from order_agent.transport.errors import install_error_handlers
from order_agent.transport.order_routes import router as order_router
from order_agent.transport.task_routes import router as task_router

CheckerPool = ConnectionPool[psycopg.Connection[dict[str, Any]]]


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_migrations("agent")
    # Retains agent-service-toolkit's pooled saver/lifespan pattern; see notices.
    # 池用 dict_row 打开，运行时行是字典；psycopg 的泛型默认按元组推导，需要显式声明。
    pool = cast(
        CheckerPool,
        ConnectionPool(
            settings().database_url.get_secret_value(),
            min_size=1,
            max_size=8,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "prepare_threshold": 0,
            },
        ),
    )
    with pool:
        pool.wait()
        saver = PostgresSaver(pool)
        saver.setup()  # LangGraph owns its checkpoint schema migrations.
        app.state.graph = build_graph(saver)
        app.state.tasks = TaskService(app.state.graph)
        try:
            yield
        finally:
            # HTTP连接属于进程，不能在每个节点结束时销毁；正常停服且请求已排空后再统一关闭。
            await asyncio.to_thread(provider_pool.close)
            await asyncio.to_thread(close_pools)


app = FastAPI(title="OrderFlow · 订单异常协同", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(order_router)
app.include_router(task_router)

WEB = Path(__file__).resolve().parents[3] / "web"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/health", tags=["health"])
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {
        "status": "ok",
        "service": "agent",
        "planner_mode": settings().planner_mode,
        "faults_enabled": settings().enable_faults,
        "pid": os.getpid(),
        "instance_id": os.environ.get("INSTANCE_ID", "local"),
    }
