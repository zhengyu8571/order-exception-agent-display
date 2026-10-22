"""HTTP contracts shared by Agent and simulated ERP boundaries."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from order_agent.domain.errors import (
    BusinessConflict,
    BusinessUnavailable,
    TaskBusy,
    TaskNotFound,
)
from order_agent.transport.errors import install_error_handlers


@pytest.mark.parametrize(
    "error,status,code",
    [
        (TaskNotFound("missing"), 404, "TASK_NOT_FOUND"),
        (TaskBusy("running"), 409, "TASK_BUSY"),
        (BusinessUnavailable("unknown result"), 503, "BUSINESS_UNAVAILABLE"),
        (
            BusinessConflict({"message": "invalid", "errors": ["stock"]}),
            409,
            "BUSINESS_CONFLICT",
        ),
    ],
)
def test_domain_failure_keeps_detail_and_stable_code(error, status, code):
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/failure")
    def fail():
        raise error

    response = TestClient(app).get("/failure")
    assert response.status_code == status
    assert response.json() == {"detail": error.detail, "code": code}
