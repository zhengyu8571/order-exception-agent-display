import httpx

from order_agent.config import settings
from order_agent.domain.errors import (
    BusinessConflict,
    BusinessRecordNotFound,
    BusinessUnavailable,
)


class OrderTools:
    """HTTP boundary deliberately separates business commit from checkpoint commit."""

    def request(
        self, method: str, path: str, *, body=None, timeout=8.0, missing_ok=False
    ):
        try:
            response = httpx.request(
                method,
                settings().order_service_url + path,
                headers={
                    "Authorization": "Bearer "
                    + settings().service_token.get_secret_value()
                },
                json=body,
                timeout=timeout,
                trust_env=False,
            )
        except httpx.TransportError as exc:
            raise BusinessUnavailable(
                "业务服务连接失败或结果未知，可在服务恢复后继续任务"
            ) from exc
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code == 404:
            raise BusinessRecordNotFound("订单或业务记录不存在")
        if response.status_code == 409:
            raise BusinessConflict(str(response.json().get("detail", "业务状态变化")))
        if response.is_error:
            raise BusinessUnavailable(
                f"业务接口返回 {response.status_code}，未确认执行成功"
            )
        return response.json()

    def order(self, order_id: str):
        return self.request("GET", f"/orders/{order_id}")

    def context(self, order_id: str):
        return self.request("GET", f"/orders/{order_id}/planning-context")

    def apply_adaptive(self, order_id: str, payload: dict):
        return self.request(
            "POST",
            f"/orders/{order_id}/adaptive-delivery-plan",
            body=payload,
            timeout=0.2 if payload["fault"] == "timeout_after_commit" else 8.0,
        )

    def stock(self, order_id: str):
        return self.request("GET", f"/orders/{order_id}/stock")

    def arrivals(self, order_id: str):
        return self.request("GET", f"/orders/{order_id}/arrivals")

    def constraints(self, order_id: str):
        return self.request("GET", f"/orders/{order_id}/constraints")

    def operation(self, operation_id: str):
        return self.request("GET", f"/operations/{operation_id}", missing_ok=True)

    def apply(self, order_id: str, payload: dict):
        return self.request(
            "POST",
            f"/orders/{order_id}/delivery-plan",
            body=payload,
            timeout=0.2 if payload["fault"] == "timeout_after_commit" else 8.0,
        )
