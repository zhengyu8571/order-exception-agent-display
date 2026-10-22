from datetime import date as calendar_date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Scenario(BaseModel):
    profile: Literal["A", "B", "C", "D", "E", "F"] | None = None
    id: str | None = Field(default=None, max_length=100)
    allow_split: bool = False
    arrival_days: int = Field(default=3, ge=0, le=365)
    due_days: int = Field(default=5, ge=0, le=365)


class BusinessChange(BaseModel):
    remote_quantity: int = Field(default=0, ge=0, le=10000)


class ResourceChange(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    target: Literal["warehouse", "route", "batch", "policy", "local", "order"]
    resource_id: str = Field(default="", max_length=100)
    quantity: int | None = Field(default=None, ge=0, le=100000)
    transfer_days: int | None = Field(default=None, ge=0, le=365)
    unit_cost_cents: int | None = Field(default=None, ge=0, le=10000000)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    allow_split: bool | None = None
    status: Literal["EXPECTED", "CANCELLED"] | None = None

    @model_validator(mode="after")
    def valid_change(self):
        allowed = {
            "warehouse": {"quantity"},
            "route": {"transfer_days", "unit_cost_cents"},
            "batch": {"quantity", "date", "status"},
            "policy": {"allow_split", "quantity", "date"},
            "local": {"quantity"},
            "order": {"date"},
        }[self.target]
        fields = {
            key
            for key, value in self.model_dump().items()
            if key not in {"target", "resource_id"} and value is not None
        }
        if not fields or not fields <= allowed:
            raise ValueError("变更字段不适用于该资源")
        if (self.target in {"warehouse", "route", "batch"}) != bool(self.resource_id):
            raise ValueError("仓库、路线、采购批次必须指定资源 ID；其他目标不能指定")
        if self.date:
            calendar_date.fromisoformat(self.date)
        return self
