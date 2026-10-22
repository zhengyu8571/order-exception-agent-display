"""HTTP boundary of the independent simulated ERP service."""

import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from order_agent.config import settings
from order_agent.domain.contracts import ApplyPlan
from order_agent.domain.decisions import AdaptiveApply
from order_agent.persistence.database import connection
from order_agent.persistence.migrations import apply_migrations
from order_agent.simulator import planning, service
from order_agent.simulator.contracts import BusinessChange, ResourceChange, Scenario
from order_agent.transport.errors import install_error_handlers


def service_auth(authorization: str = Header(default="")) -> None:
    expected = "Bearer " + settings().service_token.get_secret_value()
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "业务服务需要有效的服务凭据")


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_migrations("business")
    service.seed_scenario(Scenario(id="SO-2026-001"))
    service.seed_scenario(Scenario(id="SO-2026-002", allow_split=True))
    service.seed_scenario(Scenario(id="SO-2026-003", arrival_days=8))
    yield


app = FastAPI(title="OrderFlow · 模拟订单服务", lifespan=lifespan)
install_error_handlers(app)


@app.get("/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {
        "status": "ok",
        "service": "orders",
        "pid": os.getpid(),
        "instance_id": os.environ.get("INSTANCE_ID", "local"),
    }


@app.get("/orders", dependencies=[Depends(service_auth)])
def orders():
    return service.list_orders()


@app.get("/orders/{order_id}", dependencies=[Depends(service_auth)])
def order(order_id: str):
    return service.query_order(order_id)


@app.get("/orders/{order_id}/stock", dependencies=[Depends(service_auth)])
def stock(order_id: str):
    return service.query_stock(order_id)


@app.get("/orders/{order_id}/arrivals", dependencies=[Depends(service_auth)])
def arrivals(order_id: str):
    return service.query_arrivals(order_id)


@app.get("/orders/{order_id}/constraints", dependencies=[Depends(service_auth)])
def constraints(order_id: str):
    return service.query_constraints(order_id)


@app.get("/operations/{operation_id}", dependencies=[Depends(service_auth)])
def operation(operation_id: str):
    return service.query_operation(operation_id)


@app.post("/orders/{order_id}/delivery-plan", dependencies=[Depends(service_auth)])
def apply_plan(order_id: str, body: ApplyPlan):
    return service.apply_plan(order_id, body)


@app.post("/demo/scenarios", dependencies=[Depends(service_auth)])
def create_scenario(body: Scenario):
    return service.create_scenario(body)


@app.post("/demo/orders/{order_id}/change", dependencies=[Depends(service_auth)])
def change_order(order_id: str, body: BusinessChange):
    return service.change_order(order_id, body)


@app.get("/orders/{order_id}/planning-context", dependencies=[Depends(service_auth)])
def context(order_id: str):
    return planning.planning_context(order_id)


@app.get(
    "/orders/{order_id}/investigation/routes/{warehouse_id}",
    dependencies=[Depends(service_auth)],
)
def routes(order_id: str, warehouse_id: str):
    return planning.investigate(order_id, "route", warehouse_id)


@app.get(
    "/orders/{order_id}/investigation/batches/{batch_id}",
    dependencies=[Depends(service_auth)],
)
def batch(order_id: str, batch_id: str):
    return planning.investigate(order_id, "batch", batch_id)


@app.get(
    "/orders/{order_id}/investigation/policy", dependencies=[Depends(service_auth)]
)
def policy(order_id: str):
    return planning.investigate(order_id, "policy")


@app.post(
    "/orders/{order_id}/adaptive-delivery-plan", dependencies=[Depends(service_auth)]
)
def adaptive_plan(order_id: str, body: AdaptiveApply):
    return service.apply_plan(order_id, body)


@app.post("/demo/orders/{order_id}/resources", dependencies=[Depends(service_auth)])
def change_resource(order_id: str, body: ResourceChange):
    from order_agent.simulator.resources import change_resource

    with connection() as conn:
        return change_resource(conn, order_id, body)
