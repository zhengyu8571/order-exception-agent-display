"""评测器本身也必须验收；这里的脚本化响应只证明协议，不生成真实模型效果报告。"""

from copy import deepcopy
from datetime import date, timedelta
from itertools import product

import pytest

from order_agent.domain.adaptive_rules import validate_plan
from order_agent.evaluation.dataset import load_cases, materialize
from order_agent.evaluation.harness import evaluate_baseline, run
from order_agent.evaluation.scoring import aggregate, score
from order_agent.evaluation.strategies import make_plan, oracle, resources
from order_agent.evaluation.tools import FixtureTools


def fixed_context():
    cases, _ = load_cases("all")
    return materialize(
        next(case for case in cases if case["family"] == "cost_deadline_tradeoff"),
        date(2026, 10, 20),
    )


def test_corpus_has_fixed_pilot_and_disjoint_holdout_and_valid_oracle_plans():
    all_cases, corpus_hash = load_cases("all")
    pilot, pilot_hash = load_cases("pilot")
    holdout, holdout_hash = load_cases("holdout")
    assert len(all_cases) == 40 and len(pilot) == 10 and len(holdout) == 30
    assert len({case["id"] for case in all_cases}) == 40
    assert {case["id"] for case in pilot}.isdisjoint(case["id"] for case in holdout)
    assert corpus_hash == pilot_hash == holdout_hash
    for case in all_cases:
        context = materialize(case, date(2026, 10, 20))
        result = oracle(context)
        baseline_result = evaluate_baseline(context, result)
        assert baseline_result["recommendation_legal"] == result["feasible"], case["id"]
        assert baseline_result["failure_category"] is None, case["id"]
        for reference in result["modes"].values():
            if reference:
                from order_agent.domain.decisions import AdaptiveCandidate

                assert (
                    validate_plan(
                        AdaptiveCandidate.model_validate(reference["plan"]), context
                    )[0]
                    == []
                )


@pytest.mark.parametrize("minimum_first", [1, 2, 4])
@pytest.mark.parametrize("allow_split", [True, False])
def test_oracle_matches_exhaustive_allocations_and_delivery_schedules(
    minimum_first, allow_split
):
    context = fixed_context()
    context["order"].update(required_quantity=4, due_date="2026-10-21")
    context["local_quantity"] = 2
    for row, days, cost in zip(context["warehouses"], [1, 3], [3, 1]):
        row.update(available_quantity=2, transfer_days=days, unit_cost_cents=cost)
    context["purchase_batches"] = [
        {"id": "P", "quantity": 2, "arrival_date": "2026-10-22", "status": "EXPECTED"}
    ]
    context["policy"].update(
        allow_split=allow_split,
        minimum_first_quantity=minimum_first,
        last_delivery_date="2026-10-24",
    )
    truth = oracle(context)
    all_resources = resources(context)
    from order_agent.domain.decisions import AdaptiveCandidate

    for mode in ("FULL", "SPLIT"):
        costs = []
        for quantities in product(
            *(range(row["quantity"] + 1) for row in all_resources)
        ):
            if sum(quantities) != 4:
                continue
            # 使用 make_plan 只构造资源字段，交付时间和分批数量由独立穷举覆盖。
            shell = make_plan(
                {**context, "order": {**context["order"], "due_date": "2026-10-24"}},
                list(zip(all_resources, quantities)),
                "FULL",
            ).model_dump()
            shell["delivery_mode"] = mode
            days = range(5)
            schedules = (
                [[(4, day)] for day in days]
                if mode == "FULL"
                else [
                    [(first_q, day), (4 - first_q, later)]
                    for first_q in range(1, 4)
                    for day in days
                    for later in days
                    if later > day
                ]
            )
            for schedule in schedules:
                shell["deliveries"] = [
                    {
                        "quantity": q,
                        "delivery_date": (
                            date(2026, 10, 20) + timedelta(days=d)
                        ).isoformat(),
                    }
                    for q, d in schedule
                ]
                issues, metrics = validate_plan(
                    AdaptiveCandidate.model_validate(shell), context
                )
                if not issues:
                    costs.append(metrics["transfer_cost_cents"])
        assert (truth["modes"][mode] is not None) == bool(costs)
        if costs:
            assert truth["modes"][mode]["cost_cents"] == min(costs)


def test_baseline_is_feasible_but_cost_is_not_predeclared_optimal():
    context = fixed_context()
    truth = oracle(context)
    result = evaluate_baseline(context, truth)
    assert result["recommendation_legal"] and result["mode_transfer_cost_gap_cents"] > 0
    assert result["repair_applicable"] is False
    assert result["model_calls"] == 0 and result["token_cost_from_supplied_prices"] == 0
    assert aggregate([result])["repair_success_rate"]["value"] is None


def test_repair_uses_first_validation_not_final_evidence_and_unknown_usage_is_not_zero_cost():
    context = fixed_context()
    truth = oracle(context)
    plan = truth["modes"]["FULL"]["plan"]
    tools = FixtureTools(context)
    evidence = tools.request(
        "GET", f"/orders/{context['order']['id']}/investigation/routes/WH-003"
    )
    state = {
        "phase": "WAITING_APPROVAL",
        "evidence": {evidence["id"]: evidence},
        "adaptive_proposal": {"recommended_id": plan["id"], "candidates": [plan]},
        "decision_history": [
            {
                "action": "propose",
                "recommended_id": plan["id"],
                "candidates": [deepcopy(plan)],
            },
            {
                "action": "validation",
                "valid_ids": [],
                "feedback": [{"code": "MISSING_OR_STALE_EVIDENCE"}],
            },
            {"action": "propose", "recommended_id": plan["id"], "candidates": [plan]},
            {"action": "validation", "valid_ids": [plan["id"]]},
        ],
    }
    result = score(
        context,
        truth,
        state,
        [{"status": "STARTED", "actual_input": None, "actual_output": None}],
        [],
        1,
        prices=(2, 8),
    )
    assert result["repair_applicable"] and result["repair_succeeded"]
    assert (
        result["token_cost_from_supplied_prices"] is None
        and result["input_tokens_actual"] is None
    )

    manual = score(
        context,
        truth,
        {
            **state,
            "phase": "NEEDS_MANUAL",
            "decision_history": [*state["decision_history"], {"action": "manual"}],
        },
        [],
        [],
        1,
    )
    assert (
        manual["recommended"] is None and manual["last_proposed_candidate"] is not None
    )
    assert manual["failure_category"] == "missed_feasible_solution"


def test_baseline_run_never_opens_model_or_database(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("Baseline must not access model or DB")

    monkeypatch.setattr(
        "order_agent.evaluation.harness.prepare_test_database", forbidden
    )
    report = run("pilot", tmp_path / "result.json")
    assert len(report["cases"]) == 10 and report["agent_status"] == "NOT_RUN"
    assert (
        report["agent_summary"] is None and not report["real_model_comparison_executed"]
    )
    assert all(row["agent"] is None for row in report["cases"])


def test_fixture_tools_reject_writes_and_cross_order_reads():
    tools = FixtureTools(fixed_context())
    with pytest.raises(ValueError):
        tools.request("POST", "/orders/anything/delivery-plan")
    from order_agent.domain.errors import BusinessRecordNotFound

    with pytest.raises(BusinessRecordNotFound):
        tools.order("another-order")


@pytest.mark.integration
def test_evaluation_runs_production_graph_and_scores_rule_feedback(live, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver
    from test_adaptive_decisions import request
    from test_adaptive_integration import scripted_system

    from order_agent.config import settings
    from order_agent.evaluation.harness import evaluate_agent
    from order_agent.llm.decision_model import model_configuration
    from order_agent.persistence.database import connection

    context = fixed_context()
    truth = oracle(context)
    plan = truth["modes"]["FULL"]["plan"]

    def respond(model_context, turn):
        if not model_context["evidence"]:
            return {
                "action": "investigate",
                "requests": [request("query_transfer_routes", warehouse_id="WH-003")],
                "reason": "脚本化协议测试",
            }
        candidate = deepcopy(plan)
        if not model_context["feedback"]:
            candidate["transfers"][0]["quantity"] += 1
        return {
            "action": "propose",
            "candidates": [candidate],
            "recommended_id": candidate["id"],
            "reason": "脚本化修正测试，不是真实模型评测",
        }

    with scripted_system(live, respond):
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
            monkeypatch.setenv(key, live.env[key])
        settings.cache_clear()
        with PostgresSaver.from_conn_string(
            settings().database_url.get_secret_value()
        ) as saver:
            saver.setup()
            result = evaluate_agent(context, truth, saver, model_configuration())
        assert (
            result["recommendation_legal"]
            and result["repair_succeeded"]
            and result["model_calls"] == 3
        )
        assert all(
            row["provider"] == "scripted-test" for row in result["model_call_records"]
        )
        assert (
            len(
                [
                    row
                    for row in result["decision_history"]
                    if row["action"] == "propose" and row.get("candidates")
                ]
            )
            == 2
        )
        with connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM business.operations WHERE order_id=%s",
                    (context["order"]["id"],),
                ).fetchone()["n"]
                == 0
            )
