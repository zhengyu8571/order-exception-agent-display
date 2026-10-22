from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from order_agent.domain.errors import (
    ApplicationError,
    BusinessConflict,
    BusinessRecordNotFound,
    BusinessUnavailable,
    FaultsDisabled,
    InvalidApproval,
    TaskConflict,
    TaskNotFound,
)

HTTP_STATUS = {
    TaskNotFound: 404,
    TaskConflict: 409,
    InvalidApproval: 422,
    FaultsDisabled: 403,
    BusinessRecordNotFound: 404,
    BusinessConflict: 409,
    BusinessUnavailable: 503,
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        status = next(
            (value for kind, value in HTTP_STATUS.items() if isinstance(exc, kind)),
            500,
        )
        # Keep the v1 `detail` field; clients may additionally rely on stable codes.
        return JSONResponse(
            status_code=status,
            content={"detail": exc.detail, "code": exc.code},
        )
