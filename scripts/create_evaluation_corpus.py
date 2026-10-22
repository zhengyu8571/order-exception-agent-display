"""显式生成固定合成语料。评测运行时不会调用此脚本或根据模型结果改题。"""

import json
from pathlib import Path


def main():
    cases = []
    families = [
        "simple_transfer",
        "cost_deadline_tradeoff",
        "split_required",
        "split_forbidden",
        "cancelled_batch",
        "insufficient_total",
        "minimum_first_batch",
        "mixed_resources",
    ]
    for family in families:
        for variant, required in enumerate((80, 90, 100, 110, 120)):

            def warehouse(quantity, days=1, cost=200, identifier="WH-002"):
                return {
                    "id": identifier,
                    "quantity": quantity,
                    "days": days,
                    "cost_cents": cost,
                }

            def batch(quantity, days=3, status="EXPECTED", identifier="PO-003"):
                return {
                    "id": identifier,
                    "quantity": quantity,
                    "days": days,
                    "status": status,
                }

            case = {
                "id": f"Q{len(cases) + 1:03d}",
                "family": family,
                "pilot": variant == 0
                or (
                    variant == 1
                    and family in {"cost_deadline_tradeoff", "split_required"}
                ),
                "required_quantity": required,
                "local_quantity": required - 40,
                "due_days": 5,
                "allow_split": False,
                "minimum_first_quantity": required - 40,
                "last_days": 10,
                "warehouses": [],
                "batches": [],
            }
            if family == "simple_transfer":
                case.update(
                    local_quantity=required - 20,
                    due_days=2 + variant % 3,
                    warehouses=[warehouse(20, 1 + variant % 2, 100 + variant * 50)],
                )
            elif family == "cost_deadline_tradeoff":
                case["warehouses"] = [
                    warehouse(40, 1, 400 + variant * 50),
                    warehouse(40, 3 + variant % 2, 80 + variant * 10, "WH-003"),
                ]
            elif family in {"split_required", "split_forbidden"}:
                case.update(
                    allow_split=family == "split_required",
                    warehouses=[warehouse(10, 7 + variant % 2)],
                    batches=[
                        batch(20, 2),
                        batch(20, 8 + variant % 2, identifier="PO-004"),
                    ],
                )
            elif family == "cancelled_batch":
                case.update(
                    warehouses=[warehouse(40, 2 + variant % 2, 120 + variant * 20)],
                    batches=[
                        batch(40, 1, "CANCELLED"),
                        batch(10, 7, identifier="PO-004"),
                    ],
                )
            elif family == "insufficient_total":
                case.update(
                    local_quantity=required - 50,
                    warehouses=[warehouse(10)],
                    batches=[batch(20)],
                )
            elif family == "minimum_first_batch":
                case.update(
                    local_quantity=required - 70,
                    allow_split=True,
                    due_days=3,
                    minimum_first_quantity=required - 40,
                    warehouses=[warehouse(20, 6)],
                    batches=[batch(60, 8)],
                )
            else:
                case.update(
                    local_quantity=required - 50,
                    warehouses=[warehouse(20, 1 + variant % 2, 150 + variant * 25)],
                    batches=[batch(40, 3 + variant % 2)],
                )
            cases.append(case)
    path = Path(__file__).resolve().parents[1] / "src/order_agent/evaluation/cases.json"
    path.write_text(
        json.dumps(
            {"version": 1, "source": "synthetic-personal-project", "cases": cases},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Fixed corpus: {len(cases)} cases, {sum(row['pilot'] for row in cases)} pilot, remaining holdout"
    )


if __name__ == "__main__":
    main()
