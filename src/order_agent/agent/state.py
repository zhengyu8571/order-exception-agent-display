import operator
from typing import Annotated, TypedDict

from order_agent.domain.contracts import Candidate


def merge_facts(left: dict, right: dict) -> dict:
    # 四个并行查询写入不同事实键；Reducer 合并增量，避免后完成的节点覆盖其他查询结果。
    return {**left, **right}


class State(TypedDict, total=False):
    decision_protocol: str
    decision_mode: str
    model_configuration: dict
    generation: str
    # generation 隔离重新调查，revision 隔离候选版本；恢复同一轮时两者都不能随机重建。
    model_turn: int
    investigation_rounds: int
    proposal_attempts: int
    no_progress_rounds: int
    manual_challenges: int
    context: dict
    evidence: dict
    decision: dict | None
    adaptive_proposal: dict
    feedback: list[dict]
    decision_history: list[dict]
    rejection_reason: str
    task_id: str
    order_id: str
    fault: str
    revision: int
    facts: Annotated[dict, merge_facts]
    # Send 扇出的每个补充调查分支各写一条；add 让并行结果按完成顺序累积，
    # 汇合节点再统一排序、校验并清空，避免并行分支直接改证据。
    supplementary: Annotated[list[dict], operator.add]
    proposal: dict
    validation: dict
    approval: dict | None
    execution: dict | None
    phase: str
    error: str | None


def operation_id(state: State) -> str:
    # 同一批准动作在所有重试和进程恢复中使用相同 ID，不能把 request_id 或随机数当业务幂等键。
    approval = state["approval"]
    assert approval is not None, "没有批准记录就不能生成业务幂等键"
    return f"{state['task_id']}:v{state['revision']}:{approval['candidate_id']}:apply"


def selected(state: State) -> Candidate:
    approval = state["approval"]
    assert approval is not None, "没有批准记录就不能确定执行方案"
    return next(
        Candidate.model_validate(c)
        for c in state["proposal"]["candidates"]
        if c["id"] == approval["candidate_id"]
    )
