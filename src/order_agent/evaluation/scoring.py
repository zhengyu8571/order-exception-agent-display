"""判分使用确定性规则与有界参照；不让评测模型自报正确，也不混用指标分母。"""

import math
from statistics import mean, median

from order_agent.domain.adaptive_rules import validate_plan
from order_agent.domain.decisions import AdaptiveCandidate
from order_agent.evaluation.tools import completion_days


def score(
    context,
    truth,
    state,
    calls,
    tool_calls,
    elapsed_ms,
    *,
    baseline=False,
    prices=None,
    cache_price=None,
):
    proposal = state.get("adaptive_proposal", {})
    raw = next(
        (
            row
            for row in proposal.get("candidates", [])
            if row["id"] == proposal.get("recommended_id")
        ),
        None,
    )
    plan = AdaptiveCandidate.model_validate(raw) if raw else None
    issues, metrics = (
        validate_plan(plan, context, state.get("evidence", {})) if plan else ([], {})
    )
    valid = plan is not None and not issues and state.get("phase") == "WAITING_APPROVAL"
    # 转人工或预算耗尽后，State 可能保留旧提案；它不是最终交付给人的推荐。
    delivered = plan is not None and state.get("phase") == "WAITING_APPROVAL"
    history = state.get("decision_history", [])
    proposals = [
        (index, row) for index, row in enumerate(history) if row["action"] == "propose"
    ]
    first_legal = None
    if proposals:
        index, first = proposals[0]
        following = next(
            (
                row
                for row in history[index + 1 :]
                if row["action"] in {"validation", "propose"}
            ),
            None,
        )
        # 用首轮真实校验记录，而不是用最终补齐的证据回头“修正”首轮得分。
        first_legal = bool(
            following
            and following["action"] == "validation"
            and first["recommended_id"] in following.get("valid_ids", [])
        )
    manual = bool(history and history[-1]["action"] == "manual")
    if baseline:
        manual = plan is None
    if valid:
        failure = None
    elif manual:
        failure = "missed_feasible_solution" if truth["feasible"] else None
    elif any(call["status"] == "FAILED" for call in calls):
        failure = "provider_request_failure"
    elif state.get("phase") == "RECOVERABLE":
        failure = "runtime_error"
    else:
        failure = "no_legal_recommendation"
    usage_complete = all(
        call.get("actual_input") is not None and call.get("actual_output") is not None
        for call in calls
    )
    input_tokens = sum(call.get("actual_input") or 0 for call in calls)
    output_tokens = sum(call.get("actual_output") or 0 for call in calls)
    known_cost = (
        sum(
            (
                (
                    call["actual_input"]
                    - min(
                        call["actual_input"],
                        (call.get("diagnostics") or {}).get("cached_input_tokens", 0),
                    )
                )
                * prices[0]
                + min(
                    call["actual_input"],
                    (call.get("diagnostics") or {}).get("cached_input_tokens", 0),
                )
                * (cache_price if cache_price is not None else prices[0])
                + call["actual_output"] * prices[1]
            )
            / 1_000_000
            for call in calls
            if call.get("actual_input") is not None
            and call.get("actual_output") is not None
        )
        if prices
        else None
    )
    token_cost = (
        0
        if baseline
        else (known_cost if prices is not None and usage_complete else None)
    )
    reference = truth["modes"].get(plan.delivery_mode) if valid else None
    feedback = [issue for row in history for issue in row.get("feedback", [])]
    seen, duplicates = set(), 0
    for turn in history:
        for request in turn.get("requests", []):
            identity = (
                request["tool_name"],
                tuple(sorted(request["arguments"].items())),
            )
            duplicates += identity in seen
            seen.add(identity)
    return {
        "oracle_feasible": truth["feasible"],
        "status": state.get("phase"),
        "recommended": plan.model_dump() if delivered else None,
        "last_proposed_candidate": plan.model_dump() if plan else None,
        "recommendation_legal": valid,
        "legality_issues": issues if delivered else [],
        "last_candidate_issues": issues,
        "failure_category": failure,
        "manual_decision": manual,
        "correct_manual": manual and not truth["feasible"],
        "first_proposal_legal": first_legal,
        "repair_applicable": first_legal is False,
        "repair_succeeded": first_legal is False and valid,
        "feasible_repair_applicable": first_legal is False and truth["feasible"],
        "feasible_repair_succeeded": first_legal is False
        and truth["feasible"]
        and valid,
        "overall_correct": valid or (manual and not truth["feasible"]),
        "duplicate_query_requests": duplicates,
        "provider_failed_attempts": sum(call["status"] == "FAILED" for call in calls),
        "automatic_retry_attempts": sum(
            bool((call.get("diagnostics") or {}).get("application_retry"))
            for call in calls
        ),
        "rule_feedback_codes": sorted({issue["code"] for issue in feedback}),
        "transfer_cost_cents": metrics.get("transfer_cost_cents") if valid else None,
        "completion_days": completion_days(plan, context) if valid else None,
        "mode_min_transfer_cost_cents": reference["cost_cents"] if reference else None,
        "mode_transfer_cost_gap_cents": metrics["transfer_cost_cents"]
        - reference["cost_cents"]
        if reference
        else None,
        "tool_calls": len(tool_calls),
        "tool_trace": tool_calls,
        "model_calls": len(calls),
        "elapsed_ms": elapsed_ms,
        "usage_complete": usage_complete,
        "input_tokens_actual": input_tokens if usage_complete else None,
        "output_tokens_actual": output_tokens if usage_complete else None,
        "input_tokens_known": input_tokens,
        "output_tokens_known": output_tokens,
        "token_cost_from_supplied_prices": token_cost,
        "known_token_cost_estimate": 0 if baseline else known_cost,
    }


def aggregate(records):
    def percentile(values, p):
        values = sorted(values)
        return values[max(0, math.ceil(p * len(values)) - 1)] if values else None

    def mode_summary(mode):
        selected = [
            row
            for row in records
            if row["recommendation_legal"]
            and row["recommended"]["delivery_mode"] == mode
        ]
        return {
            "solved_cases": len(selected),
            "mean_transfer_cost_cents": mean(
                row["transfer_cost_cents"] for row in selected
            )
            if selected
            else None,
            "mean_completion_days": mean(row["completion_days"] for row in selected)
            if selected
            else None,
            "mean_mode_transfer_cost_gap_cents": mean(
                row["mode_transfer_cost_gap_cents"] for row in selected
            )
            if selected
            else None,
        }

    def rate(numerator, denominator):
        return {
            "numerator": numerator,
            "denominator": denominator,
            "value": numerator / denominator if denominator else None,
        }

    feasible = [row for row in records if row["oracle_feasible"]]
    infeasible = [row for row in records if not row["oracle_feasible"]]
    proposed = [row for row in records if row["recommended"] is not None]
    repaired = [row for row in records if row["repair_applicable"]]
    first_proposals = [
        row for row in records if row["first_proposal_legal"] is not None
    ]
    return {
        "cases": len(records),
        "overall_correct_rate": rate(
            sum(
                row.get(
                    "overall_correct",
                    row["recommendation_legal"] or row["correct_manual"],
                )
                for row in records
            ),
            len(records),
        ),
        "feasible_repair_success_rate": rate(
            sum(row.get("feasible_repair_succeeded", False) for row in records),
            sum(row.get("feasible_repair_applicable", False) for row in records),
        ),
        "latency_ms": {
            "median": median(row["elapsed_ms"] for row in records) if records else None,
            "p95": percentile([row["elapsed_ms"] for row in records], 0.95),
            "max": max((row["elapsed_ms"] for row in records), default=None),
        },
        "duplicate_query_requests": sum(
            row.get("duplicate_query_requests", 0) for row in records
        ),
        "provider_failed_attempts": sum(
            row.get("provider_failed_attempts", 0) for row in records
        ),
        "automatic_retry_attempts": sum(
            row.get("automatic_retry_attempts", 0) for row in records
        ),
        "mean_token_cost_estimate_complete_cases": mean(
            row["token_cost_from_supplied_prices"]
            for row in records
            if row["token_cost_from_supplied_prices"] is not None
        )
        if any(row["token_cost_from_supplied_prices"] is not None for row in records)
        else None,
        "mean_token_cost_estimate_all_cases": mean(
            row["token_cost_from_supplied_prices"] for row in records
        )
        if records
        and all(row["token_cost_from_supplied_prices"] is not None for row in records)
        else None,
        "known_token_cost_estimate_sum": sum(
            row.get("known_token_cost_estimate") or 0 for row in records
        ),
        "feasible_solution_rate": rate(
            sum(row["recommendation_legal"] for row in feasible), len(feasible)
        ),
        "final_recommendation_legal_rate": rate(
            sum(row["recommendation_legal"] for row in proposed), len(proposed)
        ),
        "correct_manual_on_infeasible_rate": rate(
            sum(row["correct_manual"] for row in infeasible), len(infeasible)
        ),
        "repair_success_rate": rate(
            sum(row["repair_succeeded"] for row in repaired), len(repaired)
        ),
        "first_proposal_legal_rate": rate(
            sum(row["first_proposal_legal"] for row in first_proposals),
            len(first_proposals),
        ),
        "mean_tool_calls": mean(row["tool_calls"] for row in records)
        if records
        else None,
        "mean_elapsed_ms": mean(row["elapsed_ms"] for row in records)
        if records
        else None,
        "mean_model_calls": mean(row["model_calls"] for row in records)
        if records
        else None,
        "usage_complete_cases": sum(row["usage_complete"] for row in records),
        "input_tokens_known": sum(row["input_tokens_known"] for row in records),
        "output_tokens_known": sum(row["output_tokens_known"] for row in records),
        "cost_and_delivery_by_mode": {
            mode: mode_summary(mode) for mode in ("FULL", "SPLIT")
        },
        "failures": {
            kind: sum(row["failure_category"] == kind for row in records)
            for kind in sorted(
                {row["failure_category"] for row in records if row["failure_category"]}
            )
        },
    }
