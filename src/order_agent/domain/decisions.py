"""Bounded choices available to the model. None of these grant write authority."""

from typing import Literal

from pydantic import Field, model_validator

from order_agent.domain.contracts import StrictModel


class InvestigationRequest(StrictModel):
    tool_name: str = Field(max_length=80)
    arguments: dict[str, str]
    purpose: str = Field(min_length=1, max_length=300)


class Transfer(StrictModel):
    warehouse_id: str = Field(max_length=50)
    quantity: int = Field(gt=0)
    evidence_id: str = Field(max_length=150)


class PurchaseAllocation(StrictModel):
    batch_id: str = Field(max_length=50)
    quantity: int = Field(gt=0)
    evidence_id: str = Field(max_length=150)


class Delivery(StrictModel):
    quantity: int = Field(gt=0)
    delivery_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class AdaptiveCandidate(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    title: str = Field(min_length=1, max_length=120)
    local_quantity: int = Field(ge=0)
    transfers: list[Transfer] = Field(max_length=5)
    purchase_allocations: list[PurchaseAllocation] = Field(max_length=5)
    delivery_mode: Literal["FULL", "SPLIT"]
    deliveries: list[Delivery] = Field(min_length=1, max_length=4)
    rationale: str = Field(min_length=1, max_length=1500)
    risks: list[str] = Field(max_length=6)


class AgentDecision(StrictModel):
    action: Literal["investigate", "propose", "manual"]
    requests: list[InvestigationRequest] = Field(default_factory=list, max_length=3)
    candidates: list[AdaptiveCandidate] = Field(default_factory=list, max_length=3)
    recommended_id: str = ""
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def coherent_action(self):
        if self.action == "investigate":
            if not self.requests or self.candidates or self.recommended_id:
                raise ValueError("investigate requires requests only")
        elif self.action == "propose":
            ids = [candidate.id for candidate in self.candidates]
            if (
                not ids
                or self.requests
                or len(set(ids)) != len(ids)
                or self.recommended_id not in ids
            ):
                raise ValueError(
                    "propose requires unique candidates and a valid recommendation"
                )
        elif self.requests or self.candidates or self.recommended_id:
            raise ValueError("manual cannot contain executable candidates or requests")
        return self


class RuleIssue(StrictModel):
    code: str
    field: str
    expected: str
    actual: str
    evidence_id: str = ""


class AdaptiveApply(StrictModel):
    expected_resource_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    approved_plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    task_id: str
    operation_id: str = Field(min_length=8, max_length=200)
    proposal_version: int = Field(ge=1)
    expected_order_version: int = Field(ge=1)
    candidate: AdaptiveCandidate
    fault: Literal["none", "timeout_after_commit"] = "none"
