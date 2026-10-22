from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Candidate(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    title: str = Field(min_length=1, max_length=120)
    local_quantity: int = Field(ge=0)
    transfer_quantity: int = Field(ge=0)
    purchase_quantity: int = Field(ge=0)
    first_delivery_quantity: int = Field(ge=0)
    completion_days: int = Field(ge=0, le=365)
    split_delivery: bool
    rationale: str = Field(min_length=1, max_length=2000)
    risks: list[str] = Field(max_length=10)


class ProposalDraft(StrictModel):
    candidates: list[Candidate] = Field(min_length=1, max_length=4)
    recommended_id: str


class TaskCreate(StrictModel):
    order_id: str = Field(min_length=1, max_length=100)
    exception_type: Literal["INSUFFICIENT_STOCK"] = "INSUFFICIENT_STOCK"
    decision_protocol: Literal["v1", "v2"] | None = None
    fault: Literal[
        "none",
        "crash_after_apply",
        "timeout_after_commit",
        "fail_stock_once",
        "crash_after_decision",
        "crash_after_approval",
    ] = "none"


class Approval(StrictModel):
    plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    resource_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    proposal_version: int = Field(ge=1)
    decision: Literal["approve", "reject"]
    candidate_id: str = ""
    comment: str = Field(default="", max_length=1000)
    request_id: str = Field(min_length=8, max_length=100)


class ApplyPlan(StrictModel):
    task_id: str
    operation_id: str = Field(min_length=8, max_length=200)
    proposal_version: int = Field(ge=1)
    expected_order_version: int = Field(ge=1)
    candidate: Candidate
    fault: Literal["none", "timeout_after_commit"] = "none"
