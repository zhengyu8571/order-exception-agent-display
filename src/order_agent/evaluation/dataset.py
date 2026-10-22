"""相对日期语料固定落盘；运行时只平移日期，不按模型结果修改案例。"""

import json
from datetime import date, timedelta
from importlib.resources import files

from order_agent.domain.bindings import digest, resource_fingerprint


def load_cases(suite="pilot"):
    corpus = json.loads(
        files("order_agent.evaluation")
        .joinpath("cases.json")
        .read_text(encoding="utf-8")
    )
    cases = corpus["cases"]
    if suite != "all":
        cases = [case for case in cases if case["pilot"] == (suite == "pilot")]
    return cases, digest(corpus)


def materialize(case, anchor: date):
    def day(offset):
        return (anchor + timedelta(days=offset)).isoformat()

    context = {
        "as_of": day(0),
        "today": day(0),
        "local_quantity": case["local_quantity"],
        "local_version": 1,
        "order": {
            "id": "EVAL-" + case["id"],
            "version": 1,
            "required_quantity": case["required_quantity"],
            "due_date": day(case["due_days"]),
        },
        "warehouses": [
            {
                "id": row["id"],
                "available_quantity": row["quantity"],
                "transfer_days": row["days"],
                "unit_cost_cents": row["cost_cents"],
                "version": 1,
                "route_version": 1,
            }
            for row in case["warehouses"]
        ],
        "purchase_batches": [
            {
                "id": row["id"],
                "quantity": row["quantity"],
                "arrival_date": day(row["days"]),
                "status": row["status"],
                "version": 1,
            }
            for row in case["batches"]
        ],
        "policy": {
            "allow_split": case["allow_split"],
            "minimum_first_quantity": case["minimum_first_quantity"],
            "last_delivery_date": day(case["last_days"]),
            "version": 1,
        },
    }
    context["resource_digest"] = resource_fingerprint(context)
    return context
