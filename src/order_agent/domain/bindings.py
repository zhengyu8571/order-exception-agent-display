"""Canonical business/evidence identities. Timestamps of reads are not facts."""

import hashlib
import json


def digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def resource_fingerprint(context: dict) -> str:
    # 订单版本只覆盖订单本身；把资源独立版本纳入摘要，才能发现“订单没变、库存已变”。
    # 不纳入 today / queried_at：单纯重新读取不应改变事实身份，日期合法性由规则另行复核。
    return digest(
        {
            "order": context["order"],
            "as_of": context["as_of"],
            "local_quantity": context["local_quantity"],
            "local_version": context.get("local_version", 1),
            "warehouses": sorted(
                [
                    {
                        **row,
                        "version": row.get("version", 1),
                        "route_version": row.get("route_version", 1),
                    }
                    for row in context["warehouses"]
                ],
                key=lambda row: row["id"],
            ),
            "purchase_batches": sorted(
                [
                    {**row, "version": row.get("version", 1)}
                    for row in context["purchase_batches"]
                ],
                key=lambda row: row["id"],
            ),
            "policy": {
                **context["policy"],
                "version": context["policy"].get("version", 1),
            },
        }
    )


def evidence_fingerprint(evidence: dict) -> str:
    return digest(
        {
            key: {name: value for name, value in record.items() if name != "queried_at"}
            for key, record in evidence.items()
        }
    )


def approval_binding(state: dict, candidate_id: str) -> dict:
    # 审批绑定真正执行的结构化动作，不能只绑定展示卡片或模型生成的解释文案。
    candidate = next(
        row
        for row in state["adaptive_proposal"]["candidates"]
        if row["id"] == candidate_id
    )
    return {
        "plan_digest": digest(candidate),
        "evidence_digest": evidence_fingerprint(state.get("evidence", {})),
        "resource_digest": resource_fingerprint(state["context"]),
    }
