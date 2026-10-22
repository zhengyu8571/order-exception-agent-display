"""Application failures independent of HTTP, database drivers and model clients."""


class ApplicationError(RuntimeError):
    code = "APPLICATION_ERROR"

    def __init__(self, detail: str | dict):
        self.detail = detail
        super().__init__(str(detail))


class TaskNotFound(ApplicationError):
    code = "TASK_NOT_FOUND"


class TaskConflict(ApplicationError):
    code = "TASK_CONFLICT"


class TaskBusy(TaskConflict):
    code = "TASK_BUSY"


class InvalidApproval(ApplicationError):
    code = "INVALID_APPROVAL"


class FaultsDisabled(ApplicationError):
    code = "FAULTS_DISABLED"


class BusinessRecordNotFound(ApplicationError):
    code = "BUSINESS_RECORD_NOT_FOUND"


class BusinessConflict(ApplicationError):
    code = "BUSINESS_CONFLICT"


class BusinessUnavailable(ApplicationError):
    code = "BUSINESS_UNAVAILABLE"


class MigrationError(RuntimeError):
    """Startup must fail rather than run against an unknown or altered schema."""
