from fastapi import APIRouter, Depends

from order_agent.integrations.order_client import OrderTools
from order_agent.simulator.contracts import ResourceChange, Scenario
from order_agent.transport.auth import identity, supervisor

router = APIRouter(prefix="/api", tags=["workspace"])


@router.get("/me")
def me(role: str = Depends(identity)):
    return {"role": role}


@router.get("/orders", dependencies=[Depends(identity)])
def orders():
    return OrderTools().request("GET", "/orders")


@router.post("/demo/scenarios", dependencies=[Depends(supervisor)])
def scenario(body: Scenario | None = None):
    return OrderTools().request(
        "POST", "/demo/scenarios", body=body.model_dump() if body else {}
    )


@router.post("/demo/orders/{order_id}/change", dependencies=[Depends(supervisor)])
def change(order_id: str):
    return OrderTools().request(
        "POST", f"/demo/orders/{order_id}/change", body={"remote_quantity": 0}
    )


@router.post("/demo/orders/{order_id}/resources", dependencies=[Depends(supervisor)])
def change_resource(order_id: str, body: ResourceChange):
    return OrderTools().request(
        "POST",
        f"/demo/orders/{order_id}/resources",
        body=body.model_dump(exclude_none=True),
    )
