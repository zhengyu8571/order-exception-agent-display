"""Model-selectable READ tools. No arbitrary paths, SQL or mutation capabilities."""

from order_agent.domain.decisions import InvestigationRequest
from order_agent.domain.errors import BusinessRecordNotFound
from order_agent.integrations.order_client import OrderTools

TOOL_CATALOG = [
    {
        "name": "query_transfer_routes",
        "arguments": {"warehouse_id": "one warehouse ID from overview"},
        "purpose": "查询该仓库的调拨天数和单位运输费用",
    },
    {
        "name": "query_purchase_batch",
        "arguments": {"batch_id": "one purchase batch ID from overview"},
        "purpose": "查询采购批次的预计到货日期及数量",
    },
    {
        "name": "query_customer_policy",
        "arguments": {},
        "purpose": "查询分批许可、最小首批数量及允许的最晚补交日期",
    },
]
PARAMETERS = {
    "query_transfer_routes": {"warehouse_id"},
    "query_purchase_batch": {"batch_id"},
    "query_customer_policy": set(),
}


class InvalidInvestigation(ValueError):
    pass


class InvestigationTools:
    def __init__(self, client: OrderTools):
        self.client = client

    def validate(self, request: InvestigationRequest, overview: dict):
        expected = PARAMETERS.get(request.tool_name)
        if expected is None or set(request.arguments) != expected:
            raise InvalidInvestigation(
                "工具未获授权或参数不符合白名单；不能传入 order_id、URL 或 SQL"
            )
        if request.tool_name == "query_transfer_routes":
            known = {row["id"] for row in overview["warehouses"]}
            if request.arguments["warehouse_id"] not in known:
                raise InvalidInvestigation("仓库不属于当前订单范围")
        if request.tool_name == "query_purchase_batch":
            known = {row["id"] for row in overview["purchase_batches"]}
            if request.arguments["batch_id"] not in known:
                raise InvalidInvestigation("采购批次不属于当前订单范围")

    def invoke(self, order_id: str, request: InvestigationRequest, overview: dict):
        self.validate(request, overview)
        if request.tool_name == "query_customer_policy":
            path = f"/orders/{order_id}/investigation/policy"
        elif request.tool_name == "query_transfer_routes":
            path = f"/orders/{order_id}/investigation/routes/{request.arguments['warehouse_id']}"
        else:
            path = f"/orders/{order_id}/investigation/batches/{request.arguments['batch_id']}"
        try:
            return self.client.request("GET", path)
        except BusinessRecordNotFound as exc:
            raise InvalidInvestigation("当前订单已无该资源，请重新调查") from exc
