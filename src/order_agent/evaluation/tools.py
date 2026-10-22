"""合成只读工具适配器。模型仍经过生产 InvestigationTools 白名单，绝不提供业务写入。"""

from copy import deepcopy
from datetime import date
from threading import Lock

from order_agent.domain.errors import BusinessRecordNotFound
from order_agent.simulator.planning import evidence_record


class FixtureTools:
    def __init__(self, context):
        self._context = deepcopy(context)
        self.calls = []
        self._lock = Lock()

    def record(self, name, order_id):
        if order_id != self._context["order"]["id"]:
            raise BusinessRecordNotFound("评测不能跨订单读取")
        with self._lock:
            self.calls.append(name)

    def order(self, order_id):
        self.record("order", order_id)
        return {
            **self._context["order"],
            "status": "EXCEPTION",
            "customer": "合成评测订单",
        }

    def stock(self, order_id):
        self.record("stock", order_id)
        return {
            "local_quantity": self._context["local_quantity"],
            "remote_quantity": sum(
                row["available_quantity"] for row in self._context["warehouses"]
            ),
        }

    def arrivals(self, order_id):
        self.record("arrivals", order_id)
        return {
            "quantity": sum(
                row["quantity"]
                for row in self._context["purchase_batches"]
                if row["status"] == "EXPECTED"
            )
        }

    def constraints(self, order_id):
        self.record("constraints", order_id)
        return {
            "allow_split": self._context["policy"]["allow_split"],
            "order_version": 1,
        }

    def context(self, order_id):
        self.record("planning_context", order_id)
        return deepcopy(self._context)

    def request(self, method, path):
        prefix = f"/orders/{self._context['order']['id']}/investigation/"
        if method != "GET" or not path.startswith(prefix):
            raise ValueError("评测工具只接受当前订单的只读调查；不接受执行或审批")
        suffix = path[len(prefix) :]
        kind, resource_id = (
            ("policy", "")
            if suffix == "policy"
            else (
                ("route", suffix[7:])
                if suffix.startswith("routes/")
                else ("batch", suffix[8:])
                if suffix.startswith("batches/")
                else (None, "")
            )
        )
        if kind is None:
            raise ValueError("非白名单调查路径")
        self.record(f"{kind}:{resource_id}", self._context["order"]["id"])
        return evidence_record(self._context, kind, resource_id)


def completion_days(plan, context):
    return (
        date.fromisoformat(plan.deliveries[-1].delivery_date)
        - date.fromisoformat(context["as_of"])
    ).days
