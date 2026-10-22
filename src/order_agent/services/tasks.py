import hashlib
import json
import os
import time
from uuid import uuid4

from langgraph.types import Command, Overwrite

from order_agent.config import settings
from order_agent.domain.bindings import approval_binding
from order_agent.domain.contracts import Approval, TaskCreate
from order_agent.domain.errors import FaultsDisabled, InvalidApproval, TaskConflict
from order_agent.integrations.order_client import OrderTools
from order_agent.llm.decision_model import model_configuration
from order_agent.persistence import model_repository
from order_agent.persistence import task_repository as repository
from order_agent.persistence.task_repository import (
    consume_fault,
    event,
    set_status,
    task_lock,
    task_row,
)


def config(task_id: str):
    return {"configurable": {"thread_id": task_id}, "recursion_limit": 32}


# 只把可能长时间运行的节点写成阶段事件：模型调用最长45秒，这段时间节点还没返回，
# 业务事件无法先行；抓取类节点毫秒级完成，额外事件只会淹没真正的进度。
STREAMED_STAGES = {"propose", "supplemental", "revalidate", "execute", "verify"}


def initial_state(task_id, order_id, fault, protocol, mode, model):
    return {
        "task_id": task_id,
        "order_id": order_id,
        "fault": fault,
        "facts": {},
        "revision": 0,
        "phase": "INVESTIGATING",
        "approval": None,
        "execution": None,
        "error": None,
        "decision_protocol": protocol,
        "decision_mode": mode,
        "model_configuration": model,
        "generation": str(uuid4()),
        "model_turn": 0,
        "investigation_rounds": 0,
        "proposal_attempts": 0,
        "no_progress_rounds": 0,
        "manual_challenges": 0,
        "context": {},
        "evidence": {},
        "decision_history": [],
        "feedback": [],
        "adaptive_proposal": {},
    }


class TaskService:
    """Advance a task under its lock; HTTP only handles authentication and serialization."""

    def __init__(self, graph):
        self.graph = graph

    def detail(self, task_id: str):
        row = task_row(task_id)
        snapshot = self.graph.get_state(config(task_id))
        values = snapshot.values or {}
        running = repository.active(task_id)
        interrupted = any(task.interrupts for task in snapshot.tasks)
        if not running:
            if interrupted:
                row["status"] = "WAITING_APPROVAL"
            elif snapshot.next:
                row["status"] = "RECOVERABLE"
            elif values.get("phase") in {
                "COMPLETED",
                "REJECTED",
                "STALE",
                "BLOCKED",
                "NEEDS_MANUAL",
            }:
                row["status"] = values["phase"]
            elif row["status"] == "RUNNING":
                row["status"] = "RECOVERABLE"
        return {
            **row,
            "state": values,
            "next_nodes": list(snapshot.next),
            "active": running,
            "checkpoint_id": (snapshot.config or {})
            .get("configurable", {})
            .get("checkpoint_id"),
            "model_calls": model_repository.calls(task_id)
            if values.get("decision_protocol") == "v2"
            else [],
            "approval_context": {
                candidate_id: approval_binding(values, candidate_id)
                for candidate_id in values.get("validation", {}).get("valid_ids", [])
            }
            if values.get("decision_protocol") == "v2"
            else {},
        }

    def drive(self, task_id: str, graph_input):
        set_status(task_id, "RUNNING")
        starts: dict[str, float] = {}
        try:
            # 用 LangGraph 的 tasks 流拿节点开始/结束：慢节点执行期间也能先写进度事件。
            # durability="sync" 仍要求节点结果落库后再继续；这不是自研调度器。
            for chunk in self.graph.stream(
                graph_input,
                config(task_id),
                durability="sync",
                stream_mode="tasks",
            ):
                self._stage_event(task_id, chunk, starts)
            snapshot = self.graph.get_state(config(task_id))
            interrupted = any(task.interrupts for task in snapshot.tasks)
            phase = (
                "WAITING_APPROVAL"
                if interrupted
                else snapshot.values.get("phase", "RECOVERABLE")
            )
            set_status(task_id, phase, snapshot.values.get("error"))
        except Exception as exc:
            # Keep the pending node/checkpoint for explicit recovery; never invent completion.
            message = str(exc)[:600]
            set_status(task_id, "RECOVERABLE", message)
            event(
                task_id,
                "run.recoverable",
                {"error_type": type(exc).__name__, "message": message},
            )

    @staticmethod
    def _stage_event(task_id: str, chunk: dict, starts: dict[str, float]):
        name = chunk.get("name")
        if name not in STREAMED_STAGES:
            return
        key = str(chunk.get("id") or name)
        if "result" in chunk:
            began = starts.pop(key, None)
            event(
                task_id,
                "graph.stage.finished",
                {
                    "stage": name,
                    "duration_ms": round((time.monotonic() - began) * 1000)
                    if began is not None
                    else None,
                },
            )
        else:
            starts[key] = time.monotonic()
            event(task_id, "graph.stage.started", {"stage": name})

    def list(self):
        return [self.detail(task_id) for task_id in repository.list_task_ids()]

    def page(self, status="all", search="", offset=0, limit=20):
        page = repository.task_page(status, search, offset, limit)
        return {
            "items": [self.detail(task_id) for task_id in page["ids"]],
            "total": page["total"],
            "counts": page["counts"],
            "offset": offset,
            "limit": limit,
        }

    def create(self, body: TaskCreate, *, defer_start: bool = False):
        mode = settings().planner_mode
        protocol = body.decision_protocol or ("v2" if mode == "llm" else "v1")
        if (protocol == "v2") != (mode == "llm"):
            raise TaskConflict(
                "v2 决策需要 LLM 模式；演示模式仅运行明确标注的 v1 确定性流程"
            )
        model = model_configuration() if protocol == "v2" else None
        if body.fault != "none" and not settings().enable_faults:
            raise FaultsDisabled("故障注入未开启")
        order = OrderTools().order(body.order_id)
        if order["status"] != "EXCEPTION":
            raise TaskConflict("该订单已经完成计划调整，请选择异常订单")
        task_id = str(uuid4())
        repository.create_task(
            task_id, body.order_id, mode, body.fault, protocol, model
        )
        event(
            task_id,
            "task.created",
            {"order_id": body.order_id, "planner_mode": settings().planner_mode},
        )
        # 工作台先拿到持久任务 ID 并订阅，再显式 recover 推进；没有后台任务或自动接管。
        # 两次请求之间断线时保留 CREATED，现有 recover 的无 Checkpoint 分支可安全启动。
        if defer_start:
            return self.detail(task_id)
        with task_lock(task_id):
            self.drive(
                task_id,
                initial_state(
                    task_id, body.order_id, body.fault, protocol, mode, model
                ),
            )
        return self.detail(task_id)

    def get(self, task_id: str):
        return self.detail(task_id)

    def approve(self, task_id: str, body: Approval, role: str):
        task_row(task_id)
        payload = body.model_dump(exclude_none=True)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        with task_lock(task_id):
            snapshot = self.graph.get_state(config(task_id))
            if snapshot.values.get("revision") != body.proposal_version:
                raise TaskConflict("审批方案版本已过期")
            old = repository.find_approval(
                task_id, body.proposal_version, body.request_id
            )
            if old:
                if old["payload_hash"] != digest:
                    raise TaskConflict(
                        "该方案版本已有不同审批，或 request_id 被重复用于其他参数"
                    )
            else:
                if not any(task.interrupts for task in snapshot.tasks):
                    raise TaskConflict("任务当前不在等待审批")
                if (
                    body.decision == "approve"
                    and body.candidate_id
                    not in snapshot.values["validation"]["valid_ids"]
                ):
                    raise InvalidApproval("该方案没有通过规则校验")
                binding = None
                if (
                    body.decision == "approve"
                    and snapshot.values.get("decision_protocol") == "v2"
                ):
                    binding = approval_binding(snapshot.values, body.candidate_id)
                    if any(payload.get(key) != value for key, value in binding.items()):
                        raise InvalidApproval(
                            "审批必须绑定当前方案、证据及资源摘要，请刷新后重试"
                        )
                # 先落审批账本再恢复图；若此后进程崩溃，recover() 可读取同一批准继续执行。
                # task_lock 跨 API 进程生效，避免两个不同审批同时通过本轮检查。
                repository.save_approval(
                    task_id,
                    body.proposal_version,
                    body.request_id,
                    digest,
                    payload,
                    role,
                    binding,
                )
                # 验收专用：审批已提交，但尚未 Command(resume)。重启后应复用批准，而非重新审批。
                if snapshot.values.get(
                    "fault"
                ) == "crash_after_approval" and consume_fault(
                    task_id, "after_approval"
                ):
                    os._exit(87)
            interrupted = any(task.interrupts for task in snapshot.tasks)
            if interrupted and snapshot.values["revision"] == body.proposal_version:
                self.drive(task_id, Command(resume=payload))
            elif (
                snapshot.next
                and snapshot.values.get("revision") == body.proposal_version
            ):
                self.drive(task_id, None)
        return self.detail(task_id)

    def recover(self, task_id: str):
        row = task_row(task_id)
        with task_lock(task_id):
            snapshot = self.graph.get_state(config(task_id))
            if any(task.interrupts for task in snapshot.tasks):
                # 只有已持久化的审批可以跨过 interrupt，恢复接口本身不能代替主管批准。
                approval = repository.find_approval(
                    task_id, snapshot.values["revision"]
                )
                if approval:
                    self.drive(task_id, Command(resume=approval["decision"]))
            elif snapshot.next:
                self.drive(task_id, None)
            elif not snapshot.values:
                self.drive(
                    task_id,
                    initial_state(
                        task_id,
                        row["order_id"],
                        row["fault"],
                        row["decision_protocol"],
                        row["planner_mode"],
                        row["model_configuration"],
                    ),
                )
        return self.detail(task_id)

    def replan(self, task_id: str):
        row = task_row(task_id)
        with task_lock(task_id):
            snapshot = self.graph.get_state(config(task_id))
            if snapshot.next or snapshot.values.get("phase") not in {
                "STALE",
                "BLOCKED",
                "REJECTED",
                "NEEDS_MANUAL",
            }:
                raise TaskConflict("仅过期、无可用方案或已拒绝任务可以重新调查")
            event(task_id, "task.replanning")
            self.drive(
                task_id,
                {
                    "facts": Overwrite({}),
                    "approval": None,
                    "execution": None,
                    "proposal": {},
                    "validation": {},
                    "phase": "INVESTIGATING",
                    "error": None,
                    "fault": row["fault"],
                    "generation": str(uuid4()),
                    "model_turn": 0,
                    "investigation_rounds": 0,
                    "proposal_attempts": 0,
                    "no_progress_rounds": 0,
                    "manual_challenges": 0,
                    "context": {},
                    "evidence": {},
                    "decision_history": [],
                    "feedback": [],
                    "adaptive_proposal": {},
                    "rejection_reason": snapshot.values.get("approval", {}).get(
                        "comment", ""
                    )
                    if snapshot.values.get("approval")
                    else "",
                },
            )
        return self.detail(task_id)

    def events(self, task_id: str, after: int = 0):
        task_row(task_id)
        return repository.list_events(task_id, after)

    def require_task(self, task_id: str):
        return task_row(task_id)
