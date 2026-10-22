"""Durable accounting across graph replay, including uncertain model outcomes."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from order_agent.config import settings
from order_agent.persistence.database import connection


class ModelBudgetExceeded(RuntimeError):
    pass


def calls(task_id: str) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            "SELECT id,generation,turn,provider,model,status,reserved_input,reserved_output,"
            "actual_input,actual_output,error_type,elapsed_ms,created_at,response->>'provider_response_id' AS provider_response_id,response->'diagnostics' AS diagnostics FROM agent.model_calls "
            "WHERE task_id=%s ORDER BY created_at,id",
            (task_id,),
        ).fetchall()


def reserve(
    task_id: str,
    generation: str,
    turn: int,
    model: dict,
    digest: str,
    estimated_input: int,
):
    cfg = settings()
    with connection() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,18))", (task_id,)
        )
        completed = conn.execute(
            "SELECT * FROM agent.model_calls WHERE task_id=%s AND generation=%s AND turn=%s "
            "AND status IN ('SUCCEEDED','INVALID_OUTPUT') ORDER BY created_at DESC LIMIT 1",
            (task_id, generation, turn),
        ).fetchone()
        if completed:
            # 先复用已落库结果，再考虑调用预算；节点重跑不能无故重复生成方案并再次付费。
            if completed["input_hash"] != digest:
                raise ModelBudgetExceeded(
                    "重放的决策输入已变化，需要重新调查，不能套用旧模型结果"
                )
            return completed, True
        usage = conn.execute(
            # 调用结果未知时保留预留量。不能把超时当成供应商“肯定没处理”，然后退还全部预算。
            "SELECT count(*) AS calls, COALESCE(sum(COALESCE(actual_input,reserved_input)),0) AS input, "
            "COALESCE(sum(COALESCE(actual_output,reserved_output)),0) AS output "
            "FROM agent.model_calls WHERE task_id=%s",
            (task_id,),
        ).fetchone()
        assert usage is not None, "预算查询必须返回一行合计"
        remaining_output = cfg.model_output_budget - usage["output"]
        if (
            usage["calls"] >= cfg.model_max_calls
            or usage["input"] + estimated_input > cfg.model_input_budget
            or remaining_output < 100
        ):
            raise ModelBudgetExceeded("任务模型调用或 Token 预算已耗尽，需人工处理")
        call_id = str(uuid4())
        max_output = min(cfg.model_output_per_call, remaining_output)
        row = conn.execute(
            # 先提交 STARTED 预留，再访问外部模型；这是准入账本，不承诺供应商侧调用 exactly-once。
            "INSERT INTO agent.model_calls(id,task_id,generation,turn,provider,model,input_hash,status,reserved_input,reserved_output) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,'STARTED',%s,%s) RETURNING *",
            (
                call_id,
                task_id,
                generation,
                turn,
                model["provider"],
                model["model"],
                digest,
                estimated_input,
                max_output,
            ),
        ).fetchone()
    return row, False


def finish(
    call_id: str,
    status: str,
    *,
    result: dict | None = None,
    usage: dict | None = None,
    elapsed_ms: int = 0,
    error_type: str | None = None,
    diagnostics: dict | None = None,
):
    usage = usage or {}
    if diagnostics is not None:
        result = {**(result or {}), "diagnostics": diagnostics}
    with connection() as conn:
        conn.execute(
            "UPDATE agent.model_calls SET status=%s,response=%s,actual_input=%s,actual_output=%s,"
            "elapsed_ms=%s,error_type=%s,finished_at=now() WHERE id=%s",
            (
                status,
                Jsonb(result) if result is not None else None,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                elapsed_ms,
                error_type,
                call_id,
            ),
        )
