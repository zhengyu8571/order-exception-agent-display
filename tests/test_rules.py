import pytest

from order_agent.models import Candidate
from order_agent.planner import propose
from order_agent.rules import validate_candidate


def facts():
    return {
        "order": {
            "required_quantity": 100,
            "due_days": 5,
            "allow_split": False,
            "status": "EXCEPTION",
        },
        "stock": {"local_quantity": 60, "remote_quantity": 20, "transfer_days": 1},
        "arrivals": {"quantity": 25, "arrival_days": 3},
    }


def test_demo_proposals_are_not_blindly_approved():
    f = facts()
    draft = propose(f)
    assert validate_candidate(Candidate.model_validate(draft["candidates"][0]), f) == []
    assert "客户尚未同意分批交付" in validate_candidate(
        Candidate.model_validate(draft["candidates"][1]), f
    )


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("purchase_quantity", 19, "方案数量之和必须等于订单需求"),
        ("completion_days", 1, "完成时间早于调拨或采购可用时间"),
        ("completion_days", 6, "方案超过客户允许交期"),
        ("transfer_quantity", 21, "外仓可用库存不足"),
    ],
)
def test_hard_constraints(field, value, expected):
    f = facts()
    raw = propose(f)["candidates"][0]
    raw[field] = value
    assert expected in validate_candidate(Candidate.model_validate(raw), f)


def test_invalid_model_shape_cannot_reach_business_rules():
    with pytest.raises(ValueError):
        Candidate.model_validate({"id": "unsafe", "local_quantity": "100"})
