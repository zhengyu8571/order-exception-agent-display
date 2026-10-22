import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from order_agent.domain.contracts import Approval, TaskCreate
from order_agent.services.tasks import TaskService
from order_agent.transport.auth import identity, supervisor

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# 进度流是“持久事件日志 + 轮询”，不是把图执行搬到 SSE 连接里：
# 图在驱动请求的线程中推进，跨进程时只有数据库能作为共同事实来源。
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0
IDLE_TIMEOUT_SECONDS = 300.0


def task_service(request: Request) -> TaskService:
    return request.app.state.tasks


@router.get("", dependencies=[Depends(identity)])
def list_tasks(service: TaskService = Depends(task_service)):
    return service.list()


@router.post("", dependencies=[Depends(identity)])
def create_task(
    body: TaskCreate,
    defer_start: bool = False,
    service: TaskService = Depends(task_service),
):
    return service.create(body, defer_start=defer_start)


@router.get("/page", dependencies=[Depends(identity)])
def page_tasks(
    status: Literal["all", "WAITING_APPROVAL", "COMPLETED", "attention"] = "all",
    search: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=20, ge=1, le=50),
    service: TaskService = Depends(task_service),
):
    return service.page(status, search, offset, limit)


@router.get("/{task_id}", dependencies=[Depends(identity)])
def get_task(task_id: str, service: TaskService = Depends(task_service)):
    return service.detail(task_id)


@router.post("/{task_id}/approval")
def approve(
    task_id: str,
    body: Approval,
    role: str = Depends(supervisor),
    service: TaskService = Depends(task_service),
):
    return service.approve(task_id, body, role)


@router.post("/{task_id}/recover", dependencies=[Depends(identity)])
def recover(task_id: str, service: TaskService = Depends(task_service)):
    return service.recover(task_id)


@router.post("/{task_id}/replan", dependencies=[Depends(identity)])
def replan(task_id: str, service: TaskService = Depends(task_service)):
    return service.replan(task_id)


@router.get("/{task_id}/events", dependencies=[Depends(identity)])
def events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    service: TaskService = Depends(task_service),
):
    return service.events(task_id, after)


@router.get("/{task_id}/stream", dependencies=[Depends(identity)])
async def stream(
    task_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    service: TaskService = Depends(task_service),
):
    await asyncio.to_thread(service.require_task, task_id)

    async def generate():
        cursor = after
        idle_seconds = 0.0
        last_heartbeat = time.monotonic()
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(service.events, task_id, cursor)
            if rows:
                idle_seconds = 0.0
                for row in rows:
                    cursor = row["id"]
                    data = json.dumps(row, default=str, ensure_ascii=False)
                    yield f"id: {cursor}\nevent: progress\ndata: {data}\n\n"
            else:
                idle_seconds += POLL_SECONDS
            # 心跳只用于保活，不必每 0.5 秒发一次；没有新事件时按 15 秒节流。
            if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            # 原来固定 120 次（60 秒）会强制客户端周期性重连；改为按空闲时长收尾。
            if idle_seconds >= IDLE_TIMEOUT_SECONDS:
                return
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
