import os

from langgraph.types import interrupt

from order_agent.agent.state import State, operation_id, selected
from order_agent.domain.contracts import Candidate
from order_agent.domain.errors import BusinessConflict, BusinessUnavailable
from order_agent.domain.rules import validate_candidate
from order_agent.integrations.order_client import OrderTools
from order_agent.llm.planner import propose
from order_agent.persistence.task_repository import consume_fault, event


class WorkflowNodes:
    """Node implementations; graph wiring lives separately in graph.py."""

    def __init__(self, tools: OrderTools):
        self.tools = tools

    def fetch(self, source: str):
        def node(state: State):
            if (
                source == "stock"
                and state.get("fault") == "fail_stock_once"
                and consume_fault(state["task_id"], "stock")
            ):
                raise BusinessUnavailable(
                    "已注入一次库存查询失败；其他成功查询将由 pending writes 保留"
                )
            value = getattr(self.tools, source)(state["order_id"])
            event(state["task_id"], "investigation.completed", {"source": source})
            return {"facts": {source: value}}

        return node

    def propose_node(self, state: State):
        draft = propose(state["facts"], mode=state.get("decision_mode", "demo"))
        revision = state.get("revision", 0) + 1
        event(state["task_id"], "proposal.created", {"version": revision})
        return {
            "proposal": draft,
            "revision": revision,
            "phase": "VALIDATING",
            "error": None,
        }

    def validate_node(self, state: State):
        findings = {}
        seen = set()
        for raw in state["proposal"]["candidates"]:
            candidate = Candidate.model_validate(raw)
            errors = validate_candidate(candidate, state["facts"])
            if candidate.id in seen:
                errors.append("方案 ID 重复")
            seen.add(candidate.id)
            findings[candidate.id] = errors
        if len(seen) != len(state["proposal"]["candidates"]):
            findings = {key: ["模型输出了重复方案 ID"] for key in findings}
        if (
            state["facts"]["constraints"]["order_version"]
            != state["facts"]["order"]["version"]
        ):
            findings = {
                key: ["调查期间订单版本发生变化，请重新调查"] for key in findings
            }
        valid = [key for key, errors in findings.items() if not errors]
        phase = "WAITING_APPROVAL" if valid else "BLOCKED"
        event(
            state["task_id"],
            "validation.completed",
            {"valid_ids": valid, "phase": phase},
        )
        return {
            "validation": {"findings": findings, "valid_ids": valid},
            "phase": phase,
        }

    def approval_node(self, state: State):
        # interrupt 恢复时可能重新进入本节点，因此它前面不能放不可重复的业务写操作。
        response = interrupt(
            {
                "type": "ORDER_PLAN_APPROVAL",
                "task_id": state["task_id"],
                "proposal_version": state["revision"],
                "proposal": state["proposal"],
                "valid_ids": state["validation"]["valid_ids"],
            }
        )
        if response["proposal_version"] != state["revision"]:
            raise ValueError("审批版本与当前图状态不一致")
        if (
            response["decision"] == "approve"
            and response["candidate_id"] not in state["validation"]["valid_ids"]
        ):
            raise ValueError("不能批准未通过规则校验的方案")
        event(
            state["task_id"],
            "approval.received",
            {"decision": response["decision"], "version": state["revision"]},
        )
        return {
            "approval": response,
            "phase": "APPROVED" if response["decision"] == "approve" else "REJECTED",
        }

    def revalidate_node(self, state: State):
        order = self.tools.order(state["order_id"])
        if order["version"] != state["facts"]["order"]["version"]:
            event(
                state["task_id"],
                "execution.stale",
                {"current_version": order["version"]},
            )
            return {
                "phase": "STALE",
                "error": "审批等待期间订单已变化，请重新调查和审批",
            }
        fresh = {
            "order": order,
            "stock": self.tools.stock(state["order_id"]),
            "arrivals": self.tools.arrivals(state["order_id"]),
        }
        errors = validate_candidate(selected(state), fresh)
        if errors:
            return {"phase": "STALE", "error": "；".join(errors)}
        event(state["task_id"], "execution.revalidated")
        return {"phase": "EXECUTING"}

    def execute_node(self, state: State):
        op_id = operation_id(state)
        existing = self.tools.operation(op_id)
        payload = {
            "task_id": state["task_id"],
            "operation_id": op_id,
            "proposal_version": state["revision"],
            "expected_order_version": state["facts"]["order"]["version"],
            "candidate": selected(state).model_dump(),
            "fault": "timeout_after_commit"
            if state.get("fault") == "timeout_after_commit"
            else "none",
        }
        try:
            # Even the ledger-recovery path asks the business service to compare the payload hash.
            result = self.tools.apply(state["order_id"], payload)
        except BusinessConflict as exc:
            return {"phase": "STALE", "error": str(exc)}
        if state.get("fault") == "crash_after_apply" and not existing:
            event(state["task_id"], "fault.crash_after_commit", {"operation_id": op_id})
            os._exit(86)
        event(
            state["task_id"],
            "execution.reconciled" if existing else "execution.applied",
            {"operation_id": op_id},
        )
        return {"execution": result, "phase": "VERIFYING", "error": None}

    def verify_node(self, state: State):
        order = self.tools.order(state["order_id"])
        record = self.tools.operation(operation_id(state))
        expected = selected(state).model_dump()
        execution = state["execution"]
        assert execution is not None, "verify 只在 execute 返回结果后执行"
        if (
            not record
            or order["delivery_plan"] != expected
            or order["version"] != execution["order_version"]
        ):
            raise BusinessUnavailable(
                "业务结果验证未通过，请核查实际订单和操作账本后恢复"
            )
        event(
            state["task_id"],
            "verification.completed",
            {"order_version": order["version"]},
        )
        return {"phase": "COMPLETED", "error": None}
