import hashlib
import json

from psycopg.types.json import Jsonb

from order_agent.persistence.database import connection
from order_agent.persistence.model_repository import ModelBudgetExceeded


def reserve(task_id, generation, request):
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    with connection() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,19))", (task_id,)
        )
        old = conn.execute(
            "SELECT * FROM agent.investigations WHERE task_id=%s AND generation=%s AND request_hash=%s",
            (task_id, generation, digest),
        ).fetchone()
        if old and old["status"] == "SUCCEEDED":
            return digest, old["result"]
        counts = conn.execute(
            "SELECT COALESCE(sum(attempts),0) AS total, COALESCE(sum(attempts) FILTER(WHERE generation=%s),0) AS current FROM agent.investigations WHERE task_id=%s",
            (generation, task_id),
        ).fetchone()
        if counts["total"] >= 12 or counts["current"] >= 6:
            raise ModelBudgetExceeded("补充调查工具预算耗尽")
        conn.execute(
            "INSERT INTO agent.investigations(task_id,generation,request_hash,status,attempts) VALUES(%s,%s,%s,'STARTED',1) ON CONFLICT(task_id,generation,request_hash) DO UPDATE SET status='STARTED',attempts=agent.investigations.attempts+1",
            (task_id, generation, digest),
        )
    return digest, None


def finish(task_id, generation, digest, result):
    with connection() as conn:
        conn.execute(
            "UPDATE agent.investigations SET status='SUCCEEDED',result=%s WHERE task_id=%s AND generation=%s AND request_hash=%s",
            (Jsonb(result), task_id, generation, digest),
        )
