from test_decision_evaluation import fixed_context

from order_agent.evaluation.scoring import aggregate, score
from order_agent.evaluation.strategies import oracle


def test_cached_tokens_priced_and_unknown_attempt_cost_not_reported_as_zero():
    context = fixed_context()
    calls = [
        {
            "status": "SUCCEEDED",
            "actual_input": 1000,
            "actual_output": 100,
            "diagnostics": {"cached_input_tokens": 800},
        }
    ]
    result = score(
        context,
        oracle(context),
        {"phase": "NEEDS_MANUAL"},
        calls,
        [],
        12,
        prices=(0.8, 2),
        cache_price=0.16,
    )
    assert abs(result["token_cost_from_supplied_prices"] - 0.000488) < 1e-10
    calls.append({"status": "FAILED", "actual_input": None, "actual_output": None})
    partial = score(
        context,
        oracle(context),
        {"phase": "RECOVERABLE"},
        calls,
        [],
        50,
        prices=(0.8, 2),
        cache_price=0.16,
    )
    assert partial["token_cost_from_supplied_prices"] is None
    assert (
        partial["known_token_cost_estimate"]
        == result["token_cost_from_supplied_prices"]
    )
    summary = aggregate([result, partial])
    assert summary["mean_token_cost_estimate_all_cases"] is None
    assert (
        summary["mean_token_cost_estimate_complete_cases"]
        == result["token_cost_from_supplied_prices"]
    )


def test_manual_after_failed_validation_is_not_successful_feasible_repair():
    context = fixed_context()
    result = score(
        context,
        oracle(context),
        {
            "phase": "NEEDS_MANUAL",
            "decision_history": [
                {"action": "propose", "recommended_id": "p"},
                {"action": "validation", "valid_ids": []},
                {"action": "manual"},
            ],
        },
        [],
        [],
        1,
    )
    assert (
        result["feasible_repair_applicable"] and not result["feasible_repair_succeeded"]
    )
    assert aggregate([result])["overall_correct_rate"]["value"] == 0
