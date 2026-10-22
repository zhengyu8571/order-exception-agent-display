from copy import deepcopy

import pytest

from order_agent.evaluation.holdout import check_frozen, compare_rounds, manifest


def test_frozen_source_changes_are_rejected(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/order_agent/evaluation").mkdir(parents=True)
    for name in (
        "src/a.py",
        "src/order_agent/evaluation/cases.json",
        "uv.lock",
        "pyproject.toml",
    ):
        (tmp_path / name).write_text("a", encoding="utf-8")
    snapshot = manifest(tmp_path)
    (tmp_path / "src/a.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="发生变化"):
        check_frozen(tmp_path, snapshot)


def test_stability_counts_mixed_outcomes_and_unknown_cost_separately():
    result = {
        "overall_correct": True,
        "status": "WAITING_APPROVAL",
        "model_calls": 2,
        "usage_complete": True,
        "token_cost_from_supplied_prices": 0.01,
        "known_token_cost_estimate": 0.01,
    }
    first = {"cases": [{"case_id": "a", "agent": deepcopy(result)}]}
    second = deepcopy(first)
    second["cases"][0]["agent"].update(
        overall_correct=False,
        status="RECOVERABLE",
        usage_complete=False,
        token_cost_from_supplied_prices=None,
    )
    summary = compare_rounds([first, second])
    assert summary["paired_cases"] == 1 and summary["correct_in_some_rounds_only"] == 1
    assert summary["mean_token_list_price_estimate_cny"] is None
    assert summary["usage_complete_case_attempts"] == 1
