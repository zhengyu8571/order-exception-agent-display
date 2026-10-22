"""有覆盖能力的贪心基线：同一初始视图、同一只读工具，不读取判分答案。"""

from order_agent.domain.decisions import InvestigationRequest
from order_agent.evaluation.strategies import greedy_plan
from order_agent.integrations.investigation import InvestigationTools
from order_agent.simulator.planning import overview

BASELINE_VERSION = "earliest-ready-greedy-v1"


def decide(tools, order_id):
    for source in ("order", "stock", "arrivals", "constraints"):
        getattr(tools, source)(order_id)
    view = overview(tools.context(order_id))
    evidence = {}
    observed = {
        "as_of": view["as_of"],
        "today": view["today"],
        "order": view["order"],
        "local_quantity": view["local_quantity"],
        "warehouses": [],
        "purchase_batches": [],
        "policy": {
            "allow_split": False,
            "minimum_first_quantity": 0,
            "last_delivery_date": view["order"]["due_date"],
        },
    }
    # 初始视图足以识别总量不足，基线不必为显而易见的无解订单查询全部工具。
    total = (
        view["local_quantity"]
        + sum(row["available_quantity"] for row in view["warehouses"])
        + sum(
            row["quantity"]
            for row in view["purchase_batches"]
            if row["status"] == "EXPECTED"
        )
    )
    if total < view["order"]["required_quantity"]:
        return None, evidence
    investigations = InvestigationTools(tools)

    def query(name, arguments):
        result = investigations.invoke(
            order_id,
            InvestigationRequest(
                tool_name=name,
                arguments=arguments,
                purpose="确定性基线核实可用日期和成本",
            ),
            view,
        )
        evidence[result["id"]] = result
        return result["data"]

    observed["warehouses"] = [
        query("query_transfer_routes", {"warehouse_id": row["id"]})
        for row in view["warehouses"]
        if row["available_quantity"] > 0
    ]
    observed["purchase_batches"] = [
        query("query_purchase_batch", {"batch_id": row["id"]})
        for row in view["purchase_batches"]
        if row["quantity"] > 0 and row["status"] == "EXPECTED"
    ]
    plan = greedy_plan(observed)
    if plan is None and view["allow_split"]:
        observed["policy"] = query("query_customer_policy", {})
        plan = greedy_plan(observed)
    return plan, evidence
