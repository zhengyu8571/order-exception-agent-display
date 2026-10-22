"""Model explores; deterministic graph gates approval, execution and recovery."""

import json
import os
from datetime import date

from langgraph.graph import END
from langgraph.types import Overwrite, Send

from order_agent.agent.nodes import WorkflowNodes
from order_agent.agent.observations import (
    CONTRACT,
    check_manual,
    decision_context,
    evidence_query,
    query_identity,
)
from order_agent.agent.state import operation_id
from order_agent.domain.adaptive_rules import validate_plan
from order_agent.domain.bindings import approval_binding, resource_fingerprint
from order_agent.domain.decisions import (
    AdaptiveCandidate,
    AgentDecision,
    InvestigationRequest,
)
from order_agent.domain.errors import BusinessConflict, BusinessUnavailable
from order_agent.integrations.investigation import (
    TOOL_CATALOG,
    InvalidInvestigation,
    InvestigationTools,
)
from order_agent.llm.decision_model import DecisionModel, ModelCallFailed
from order_agent.persistence import investigation_repository
from order_agent.persistence.model_repository import ModelBudgetExceeded
from order_agent.persistence.task_repository import consume_fault, event
from order_agent.simulator.planning import overview


def adaptive(state):
    return state.get("decision_protocol") == "v2"


def invalid_tool_issue(exc):
    return {
        "code": "INVALID_TOOL_REQUEST",
        "field": "requests",
        "expected": "read-only allowlist and scoped IDs",
        "actual": str(exc),
    }


def chosen(state):
    return next(
        AdaptiveCandidate.model_validate(row)
        for row in state["adaptive_proposal"]["candidates"]
        if row["id"] == state["approval"]["candidate_id"]
    )


def display_candidate(candidate, context):
    """Compatibility projection for the existing cards; never used as the write action."""
    as_of = date.fromisoformat(context["as_of"])
    end = max(date.fromisoformat(row.delivery_date) for row in candidate.deliveries)
    return {
        "id": candidate.id,
        "title": candidate.title,
        "local_quantity": candidate.local_quantity,
        "transfer_quantity": sum(row.quantity for row in candidate.transfers),
        "purchase_quantity": sum(
            row.quantity for row in candidate.purchase_allocations
        ),
        "completion_days": (end - as_of).days,
        "split_delivery": candidate.delivery_mode == "SPLIT",
        "first_delivery_quantity": candidate.deliveries[0].quantity
        if candidate.delivery_mode == "SPLIT"
        else 0,
        "rationale": candidate.rationale,
        "risks": candidate.risks,
    }


class AdaptiveWorkflowNodes(WorkflowNodes):
    def __init__(self, tools, model=None):
        super().__init__(tools)
        self.model = model or DecisionModel()
        self.investigations = InvestigationTools(tools)

    def propose_node(self, state):
        if not adaptive(state):
            return super().propose_node(state)
        context = state.get("context") or self.tools.context(state["order_id"])
        if context["order"]["version"] != state["facts"]["order"]["version"]:
            return {"phase": "STALE", "error": "调查期间订单已变化，请重新调查"}
        turn = state.get("model_turn", 0)
        history = state.get("decision_history", [])
        input_context = {
            "overview": overview(context),
            "evidence": state.get("evidence", {}),
            "tools": TOOL_CATALOG,
            "feedback": state.get("feedback", []),
            "previous_proposal": state.get("adaptive_proposal"),
            "rejection_reason": state.get("approval", {}).get("comment", "")
            if state.get("approval")
            else state.get("rejection_reason", ""),
            "remaining_investigation_rounds": 2 - state.get("investigation_rounds", 0),
            "remaining_proposal_attempts": 3 - state.get("proposal_attempts", 0),
        }
        if state["model_configuration"].get("decision_contract") == CONTRACT:
            input_context = decision_context(context, state)
        try:
            result, call_id, replayed = self.model.decide(
                state["task_id"],
                state["generation"],
                turn,
                state["model_configuration"],
                input_context,
            )
        except ModelBudgetExceeded as exc:
            return {
                "phase": "NEEDS_MANUAL",
                "error": str(exc),
                "validation": {"valid_ids": [], "findings": {}},
                "context": context,
            }
        except ModelCallFailed as exc:
            raise BusinessUnavailable(str(exc)) from exc
        if state.get("fault") == "crash_after_decision" and consume_fault(
            state["task_id"], "after_model"
        ):
            os._exit(86)
        updates = {"model_turn": turn + 1, "context": context, "error": None}
        if result["decision"] is None:
            history = history + [
                {
                    "turn": turn,
                    "call_id": call_id,
                    "action": "invalid_output",
                    "feedback": result["feedback"],
                }
            ]
            event(
                state["task_id"],
                "decision.invalid_output",
                {"turn": turn, "call_id": call_id},
            )
            return {
                **updates,
                "decision_history": history,
                "feedback": result["feedback"],
                "phase": "RETHINK",
            }
        decision = AgentDecision.model_validate(result["decision"])
        history = history + [
            {
                "turn": turn,
                "call_id": call_id,
                "replayed": replayed,
                "action": decision.action,
                "reason": decision.reason,
                "requests": [r.model_dump() for r in decision.requests],
                # 保存提案原貌，评测才能区分首轮合法、失败修正和最终候选，避免只看最后一次结果。
                **(
                    {
                        "candidates": [
                            candidate.model_dump() for candidate in decision.candidates
                        ],
                        "recommended_id": decision.recommended_id,
                    }
                    if decision.action == "propose"
                    else {}
                ),
            }
        ]
        event(
            state["task_id"],
            "decision.made",
            {
                "turn": turn,
                "call_id": call_id,
                "action": decision.action,
                "reason": decision.reason,
                "replayed": replayed,
            },
        )
        updates.update(decision=decision.model_dump(), decision_history=history)
        if decision.action == "manual":
            if state["model_configuration"].get(
                "decision_contract"
            ) == CONTRACT and not state.get("manual_challenges", 0):
                issue = check_manual(input_context)
                if issue:
                    # 只反驳与已核实容量矛盾的“无解”；不调用评测 oracle，也不自动生成业务方案。
                    event(state["task_id"], "manual.challenged", issue)
                    return {
                        **updates,
                        "manual_challenges": 1,
                        "decision_history": history
                        + [{"action": "manual_check", "feedback": [issue]}],
                        "feedback": [issue],
                        "phase": "RETHINK",
                    }
            return {
                **updates,
                "phase": "NEEDS_MANUAL",
                "error": decision.reason,
                "validation": {"valid_ids": [], "findings": {}},
            }
        if decision.action == "investigate":
            if state["model_configuration"].get("decision_contract") == CONTRACT:
                completed = {
                    query_identity(evidence_query(record))
                    for record in state.get("evidence", {}).values()
                }
                fresh, selected = [], set()
                for request in decision.requests:
                    identity = query_identity(request.model_dump())
                    if identity not in completed and identity not in selected:
                        fresh.append(request.model_dump())
                        selected.add(identity)
                exhausted = state.get("investigation_rounds", 0) >= 2
                if not fresh or exhausted:
                    count = state.get("no_progress_rounds", 0) + 1
                    feedback = {
                        "code": "INVESTIGATION_LIMIT"
                        if exhausted
                        else "ALREADY_OBSERVED",
                        "field": "requests",
                        "expected": "Use confirmed evidence to propose/manual; only investigate a missing available_query",
                        "actual": "No new observation possible from this request",
                    }
                    event(
                        state["task_id"],
                        "decision.no_progress",
                        {"turn": turn, "code": feedback["code"], "consecutive": count},
                    )
                    # 缓存只能省业务查询，不能省模型循环；重复请求显式反馈，连续两轮无进展则安全退出。
                    return {
                        **updates,
                        "no_progress_rounds": count,
                        "feedback": [feedback, *state.get("feedback", [])][:12],
                        "phase": "RETHINK" if count < 2 else "NEEDS_MANUAL",
                        "error": None
                        if count < 2
                        else "连续决策未增加信息，需人工处理",
                        "validation": {"valid_ids": [], "findings": {}},
                    }
                updates["decision"] = {**decision.model_dump(), "requests": fresh}
                updates["no_progress_rounds"] = 0
            if state.get("investigation_rounds", 0) >= 2:
                return {
                    **updates,
                    "phase": "NEEDS_MANUAL",
                    "error": "补充调查轮数耗尽",
                    "validation": {"valid_ids": [], "findings": {}},
                }
            return {**updates, "phase": "SUPPLEMENTAL"}
        attempts = state.get("proposal_attempts", 0) + 1
        if attempts > 3:
            return {
                **updates,
                "phase": "NEEDS_MANUAL",
                "error": "方案修正次数耗尽",
                "validation": {"valid_ids": [], "findings": {}},
            }
        version = state.get("revision", 0) + 1
        # Date validity is checked before generating compatibility projections.
        try:
            displayed = [display_candidate(c, context) for c in decision.candidates]
        except ValueError:
            return {
                **updates,
                "proposal_attempts": attempts,
                "phase": "RETHINK" if attempts < 3 else "NEEDS_MANUAL",
                "error": None if attempts < 3 else "日期格式修正次数耗尽，需人工处理",
                "feedback": [
                    {
                        "code": "INVALID_DATE",
                        "field": "deliveries",
                        "expected": "calendar dates",
                        "actual": "invalid",
                    }
                ],
            }
        event(
            state["task_id"],
            "proposal.created",
            {"version": version, "attempt": attempts},
        )
        return {
            **updates,
            "proposal_attempts": attempts,
            "revision": version,
            "adaptive_proposal": {
                "candidates": [c.model_dump() for c in decision.candidates],
                "recommended_id": decision.recommended_id,
            },
            "proposal": {
                "candidates": displayed,
                "recommended_id": decision.recommended_id,
            },
            "phase": "VALIDATING",
        }

    def route_propose(self, state):
        """补充调查用 Send 动态扇出：扇出数量由模型本轮请求决定，不是固定分支。"""
        phase = state.get("phase")
        if phase == "VALIDATING":
            return "validate"
        if phase == "SUPPLEMENTAL":
            requests = state["decision"]["requests"]
            if not requests:
                # propose 已保证非空；真为空时直接汇合，避免图停在无触发后继的节点。
                return "investigate_join"
            return [
                Send(
                    "supplemental",
                    {
                        "task_id": state["task_id"],
                        "generation": state["generation"],
                        "order_id": state["order_id"],
                        "context": state["context"],
                        "request": request,
                    },
                )
                for request in requests
            ]
        if phase == "RETHINK":
            return "propose"
        return END

    def supplemental_node(self, payload: dict):
        """单个只读调查请求；Send 让多个请求并行执行，互不阻塞。

        分支只负责查询并带回结果，不写证据、不改阶段：合并、版本复核与清空都在
        汇合节点完成，这样并行分支之间没有写入顺序依赖。
        """
        request = InvestigationRequest.model_validate(payload["request"])
        context = payload["context"]
        summary = overview(context)
        record = {"request": request.model_dump()}
        try:
            self.investigations.validate(request, summary)
        except InvalidInvestigation as exc:
            return {"supplementary": [{**record, "error": invalid_tool_issue(exc)}]}
        # Purpose is explanatory text, not part of the query identity.
        query = {"tool_name": request.tool_name, "arguments": request.arguments}
        try:
            digest, cached = investigation_repository.reserve(
                payload["task_id"], payload["generation"], query
            )
        except ModelBudgetExceeded as exc:
            return {
                "supplementary": [
                    {
                        **record,
                        "error": {
                            "code": "TOOL_BUDGET_EXHAUSTED",
                            "field": "requests",
                            "expected": "stay within the investigation budget",
                            "actual": str(exc),
                        },
                    }
                ]
            }
        try:
            result = cached or self.investigations.invoke(
                payload["order_id"], request, summary
            )
        except InvalidInvestigation as exc:
            return {"supplementary": [{**record, "error": invalid_tool_issue(exc)}]}
        if cached is None:
            investigation_repository.finish(
                payload["task_id"], payload["generation"], digest, result
            )
        return {
            "supplementary": [
                {**record, "evidence": result, "cached": cached is not None}
            ]
        }

    def investigate_join_node(self, state):
        """汇合并行调查结果：证据、反馈和阶段只在这里写一次。"""
        context = state["context"]
        fingerprint = resource_fingerprint(context)
        evidence = dict(state.get("evidence", {}))
        feedback = [
            issue
            for issue in state.get("feedback", [])
            if issue.get("code") not in {"INVALID_MODEL_OUTPUT", "INVALID_TOOL_REQUEST"}
        ][-12:]
        stale_error = None
        budget_exhausted = False
        # 并行完成顺序不稳定；按请求内容排序，保证事件与反馈顺序可复现。
        rows = sorted(
            state.get("supplementary", []),
            key=lambda row: json.dumps(row["request"], sort_keys=True),
        )
        for row in rows:
            error = row.get("error")
            if error:
                feedback.append(error)
                budget_exhausted = budget_exhausted or (
                    error["code"] == "TOOL_BUDGET_EXHAUSTED"
                )
                continue
            result = row["evidence"]
            if result["order_version"] != context["order"]["version"]:
                stale_error = "补充调查期间业务版本变化，请重新调查"
                continue
            if result.get("resource_digest", fingerprint) != fingerprint:
                stale_error = "补充调查期间资源发生变化，请重新调查"
                continue
            evidence[result["id"]] = result
            event(
                state["task_id"],
                "investigation.supplemental",
                {
                    "tool_name": row["request"]["tool_name"],
                    "arguments": row["request"]["arguments"],
                    "purpose": row["request"].get("purpose"),
                    "evidence_id": result["id"],
                    "cached": bool(row.get("cached")),
                },
            )
        # supplementary 带 operator.add：每轮结束必须显式清空，否则下一轮会重复合并旧结果。
        cleared = {"supplementary": Overwrite([])}
        if stale_error:
            return {
                **cleared,
                "evidence": evidence,
                "phase": "STALE",
                "error": stale_error,
            }
        if budget_exhausted:
            return {
                **cleared,
                "evidence": evidence,
                "feedback": feedback,
                "phase": "NEEDS_MANUAL",
                "error": "补充调查工具预算耗尽",
                "validation": {"valid_ids": [], "findings": {}},
            }
        return {
            **cleared,
            "evidence": evidence,
            "feedback": feedback,
            "investigation_rounds": state.get("investigation_rounds", 0) + 1,
            "phase": "RETHINK",
        }

    def route_join(self, state):
        return "propose" if state.get("phase") == "RETHINK" else END

    def validate_node(self, state):
        if not adaptive(state):
            return super().validate_node(state)
        findings, issues, metrics = {}, {}, {}
        for raw in state["adaptive_proposal"]["candidates"]:
            candidate = AdaptiveCandidate.model_validate(raw)
            errors, measures = validate_plan(
                candidate, state["context"], state.get("evidence", {})
            )
            issues[candidate.id] = errors
            findings[candidate.id] = [
                f"{row['code']} · {row['field']}：期望 {row['expected']}，实际 {row['actual']}"
                for row in errors
            ]
            metrics[candidate.id] = measures
        valid = [key for key, value in findings.items() if not value]
        valid_recommendation = state["adaptive_proposal"]["recommended_id"] in valid
        phase = (
            "WAITING_APPROVAL"
            if valid_recommendation
            else "RETHINK"
            if state["proposal_attempts"] < 3
            else "NEEDS_MANUAL"
        )
        feedback = [
            {"candidate_id": key, **row}
            for key, value in issues.items()
            for row in value
        ]
        event(
            state["task_id"],
            "validation.completed",
            {"valid_ids": valid, "phase": phase, "issues": feedback},
        )
        return {
            "validation": {
                "findings": findings,
                "valid_ids": valid,
                "issues": issues,
                "metrics": metrics,
            },
            "feedback": feedback,
            "decision_history": state.get("decision_history", [])
            + [
                {
                    "action": "validation",
                    "proposal_version": state["revision"],
                    "feedback": feedback,
                    "valid_ids": valid,
                }
            ],
            "phase": phase,
            "error": "没有符合约束的方案，自动修正已耗尽"
            if phase == "NEEDS_MANUAL"
            else None,
        }

    def revalidate_node(self, state):
        if not adaptive(state):
            return super().revalidate_node(state)
        context = self.tools.context(state["order_id"])
        binding = approval_binding(state, state["approval"]["candidate_id"])
        # 审批有效意味着“批准的动作及其事实依据”都没变，而不只是 candidate_id 相同。
        if (
            context["order"]["version"] != state["context"]["order"]["version"]
            or resource_fingerprint(context) != binding["resource_digest"]
            or any(
                state["approval"].get(key) != value for key, value in binding.items()
            )
        ):
            return {
                "phase": "STALE",
                "error": "审批期间证据版本变化，必须重新调查和审批",
            }
        errors, _ = validate_plan(chosen(state), context, state.get("evidence", {}))
        if errors:
            return {
                "phase": "STALE",
                "error": "执行前复核失败：" + json.dumps(errors, ensure_ascii=False),
            }
        event(state["task_id"], "execution.revalidated")
        return {"phase": "EXECUTING"}

    def execute_node(self, state):
        if not adaptive(state):
            return super().execute_node(state)
        op_id = operation_id(state)
        existing = self.tools.operation(op_id)
        body = {
            "task_id": state["task_id"],
            "operation_id": op_id,
            "proposal_version": state["revision"],
            "expected_order_version": state["context"]["order"]["version"],
            "candidate": chosen(state).model_dump(),
            "fault": "timeout_after_commit"
            if state.get("fault") == "timeout_after_commit"
            else "none",
        }
        # 升级兼容：P2 已提交的操作沿用原参数对账；不能用此兼容分支创建无摘要的新操作。
        if existing is None or existing["result"].get("resource_digest"):
            binding = approval_binding(state, state["approval"]["candidate_id"])
            if any(
                state["approval"].get(key) != value for key, value in binding.items()
            ):
                return {"phase": "STALE", "error": "审批摘要无效，必须重新审批"}
            body.update(
                expected_resource_digest=binding["resource_digest"],
                approved_plan_digest=binding["plan_digest"],
            )
        try:
            result = self.tools.apply_adaptive(state["order_id"], body)
        except BusinessConflict as exc:
            return {"phase": "STALE", "error": str(exc)}
        if state.get("fault") == "crash_after_apply" and existing is None:
            # 真正退出进程，确保测试覆盖“业务已提交、图尚未保存节点结果”的恢复窗口。
            os._exit(86)
        event(
            state["task_id"],
            "execution.reconciled" if existing else "execution.applied",
            {"operation_id": op_id},
        )
        return {"execution": result, "phase": "VERIFYING", "error": None}

    def verify_node(self, state):
        if not adaptive(state):
            return super().verify_node(state)
        order = self.tools.order(state["order_id"])
        record = self.tools.operation(operation_id(state))
        expected = chosen(state).model_dump()
        if (
            not record
            or record["result"]["plan"] != expected
            or order["delivery_plan"] != expected
            or order["version"] != state["execution"]["order_version"]
        ):
            raise BusinessUnavailable("实际业务结果与批准动作不一致，需核查后恢复")
        event(
            state["task_id"],
            "verification.completed",
            {"order_version": order["version"]},
        )
        return {"phase": "COMPLETED", "error": None}
