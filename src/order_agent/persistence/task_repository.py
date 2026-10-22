import json
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from order_agent.domain.errors import TaskBusy, TaskNotFound
from order_agent.persistence.database import connection


def event(task_id: str, kind: str, data: dict | None = None):
    with connection() as conn:
        # ID 在 INSERT 时分配，不代表提交顺序。先串行化同任务事件，再分配 ID，
        # 后写者只能在前写者提交/回滚后进入，SSE 的 after 游标才不会越过未提交事件。
        # 独立锁空间不能复用推进任务的会话锁，否则图内并行节点会等待自己。
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,20))", (task_id,)
        )
        conn.execute(
            "INSERT INTO agent.events(task_id,type,data) VALUES(%s,%s,%s)",
            (task_id, kind, Jsonb(data or {})),
        )
    print(
        json.dumps(
            {"task_id": task_id, "event": kind, **(data or {})}, ensure_ascii=False
        ),
        flush=True,
    )


def consume_fault(task_id: str, point: str) -> bool:
    with connection() as conn:
        return (
            conn.execute(
                "INSERT INTO agent.fault_hits VALUES(%s,%s) ON CONFLICT DO NOTHING RETURNING task_id",
                (task_id, point),
            ).fetchone()
            is not None
        )


def task_row(task_id: str):
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM agent.tasks WHERE id=%s", (task_id,)
        ).fetchone()
    if not row:
        raise TaskNotFound("任务不存在")
    return row


def set_status(task_id: str, status: str, error: str | None = None):
    with connection() as conn:
        conn.execute(
            "UPDATE agent.tasks SET status=%s,last_error=%s,updated_at=now() WHERE id=%s",
            (status, error, task_id),
        )


def active(task_id: str) -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,1)) AS got",
            (task_id,),
        ).fetchone()
    assert row is not None, "advisory lock 查询必须返回一行"
    return not row["got"]


def list_task_ids() -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id FROM agent.tasks ORDER BY updated_at DESC LIMIT 60"
        ).fetchall()
    return [row["id"] for row in rows]


def task_page(status: str, search: str, offset: int, limit: int):
    clauses, parameters = [], []
    if status == "attention":
        # 硬退出可能留下 RUNNING 登记状态，必须保留在待处理集合，详情再结合图和锁判断。
        clauses.append(
            "status IN ('CREATED','RUNNING','RECOVERABLE','STALE','BLOCKED','REJECTED','NEEDS_MANUAL')"
        )
    elif status != "all":
        clauses.append("status=%s")
        parameters.append(status)
    if search:
        clauses.append("(id ILIKE %s ESCAPE '!' OR order_id ILIKE %s ESCAPE '!')")
        escaped = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        parameters.extend(["%" + escaped + "%"] * 2)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection() as conn:
        # 页内总数与条目来自同一只读快照；刷新时任务顺序仍可随业务推进而变化。
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        total_row = conn.execute(
            "SELECT count(*) AS n FROM agent.tasks" + where, parameters
        ).fetchone()
        assert total_row is not None, "count 查询必须返回一行"
        total = total_row["n"]
        rows = conn.execute(
            "SELECT id FROM agent.tasks"
            + where
            + " ORDER BY updated_at DESC,id DESC LIMIT %s OFFSET %s",
            [*parameters, limit, offset],
        ).fetchall()
        counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status,count(*) AS n FROM agent.tasks GROUP BY status"
            ).fetchall()
        }
    return {"ids": [row["id"] for row in rows], "total": total, "counts": counts}


def create_task(
    task_id: str,
    order_id: str,
    planner_mode: str,
    fault: str,
    protocol: str = "v1",
    model: dict | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent.tasks(id,order_id,status,planner_mode,fault,decision_protocol,model_configuration) "
            "VALUES(%s,%s,'CREATED',%s,%s,%s,%s)",
            (
                task_id,
                order_id,
                planner_mode,
                fault,
                protocol,
                Jsonb(model) if model else None,
            ),
        )


def find_approval(task_id: str, version: int, request_id: str | None = None):
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM agent.approvals WHERE task_id=%s "
            "AND (proposal_version=%s OR request_id=%s)",
            (task_id, version, request_id),
        ).fetchone()


def save_approval(
    task_id: str,
    version: int,
    request_id: str,
    digest: str,
    payload: dict,
    role: str,
    binding: dict | None = None,
) -> None:
    # Caller holds the task's session lock across validation, commit and graph resume.
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent.approvals(task_id,proposal_version,request_id,payload_hash,decision,decided_by,plan_digest,evidence_digest,resource_digest) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                task_id,
                version,
                request_id,
                digest,
                Jsonb(payload),
                role,
                (binding or {}).get("plan_digest"),
                (binding or {}).get("evidence_digest"),
                (binding or {}).get("resource_digest"),
            ),
        )


def list_events(task_id: str, after: int) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM agent.events WHERE task_id=%s AND id>%s ORDER BY id LIMIT 300",
            (task_id, after),
        ).fetchall()


@contextmanager
def task_lock(task_id: str):
    # A session advisory lock protects graph/approval mutation across API processes; death releases it.
    # 连接来自进程池，autocommit 属于连接状态，必须在离开本块前恢复，不能污染下一位使用者。
    with connection() as conn:
        conn.autocommit = True
        try:
            locked_row = conn.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,1)) AS locked",
                (task_id,),
            ).fetchone()
            assert locked_row is not None, "advisory lock 查询必须返回一行"
            if not locked_row["locked"]:
                raise TaskBusy("任务正在执行，请稍后重试")
            try:
                yield
            finally:
                conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,1))", (task_id,)
                )
        finally:
            conn.autocommit = False
