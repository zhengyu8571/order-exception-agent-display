"""复用生产图和模型适配，评测只运行到审批边界，不批准或修改任何订单。"""

import hashlib
import importlib.metadata
import json
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from order_agent.agent.graph import build_graph
from order_agent.config import settings
from order_agent.domain.bindings import digest
from order_agent.domain.decisions import AgentDecision
from order_agent.evaluation import baseline
from order_agent.evaluation.dataset import load_cases, materialize
from order_agent.evaluation.scoring import aggregate, score
from order_agent.evaluation.strategies import oracle
from order_agent.evaluation.tools import FixtureTools
from order_agent.integrations.investigation import TOOL_CATALOG
from order_agent.llm.decision_model import SYSTEM_PROMPT, model_configuration
from order_agent.persistence import task_repository
from order_agent.persistence.migrations import apply_migrations
from order_agent.services.tasks import TaskService, initial_state


def evaluate_baseline(context, truth):
    tools = FixtureTools(context)
    started = time.perf_counter()
    plan, evidence = baseline.decide(tools, context["order"]["id"])
    state = {
        "phase": "WAITING_APPROVAL" if plan else "NEEDS_MANUAL",
        "evidence": evidence,
        "adaptive_proposal": {
            "recommended_id": plan.id,
            "candidates": [plan.model_dump()],
        }
        if plan
        else {},
    }
    return score(
        context,
        truth,
        state,
        [],
        tools.calls,
        (time.perf_counter() - started) * 1000,
        baseline=True,
    )


def evaluate_agent(
    context, truth, saver, configuration, prices=None, model=None, cache_price=None
):
    tools = FixtureTools(context)
    graph = build_graph(saver, tools=tools, model=model)
    task_id = str(uuid4())
    task_repository.create_task(
        task_id, context["order"]["id"], "llm", "none", "v2", configuration
    )
    started = time.perf_counter()
    service = TaskService(graph)
    with task_repository.task_lock(task_id):
        service.drive(
            task_id,
            initial_state(
                task_id, context["order"]["id"], "none", "v2", "llm", configuration
            ),
        )
    task = service.detail(task_id)
    scoring_state = {
        **task["state"],
        "phase": task["status"]
        if task["status"] == "RECOVERABLE"
        else task["state"].get("phase"),
    }
    result = score(
        context,
        truth,
        scoring_state,
        task["model_calls"],
        tools.calls,
        (time.perf_counter() - started) * 1000,
        prices=prices,
        cache_price=cache_price,
    )
    result.update(
        task_id=task_id,
        checkpoint_id=task["checkpoint_id"],
        decision_history=task["state"].get("decision_history", []),
        model_call_records=task["model_calls"],
        evidence=task["state"].get("evidence", {}),
        error=task["state"].get("error"),
    )
    return result


def prepare_test_database():
    cfg = settings()
    base = cfg.database_url.get_secret_value()
    database = conninfo_to_dict(base).get("dbname", "order_agent")
    dsn = os.environ.get("TEST_DATABASE_URL") or make_conninfo(
        base, dbname=database if database.endswith("_test") else database + "_test"
    )
    if not conninfo_to_dict(dsn).get("dbname", "").endswith("_test"):
        raise ValueError("决策评测只允许 _test 数据库，不得写入工作台演示库")
    os.environ["DATABASE_URL"] = dsn
    settings.cache_clear()
    apply_migrations("agent")
    return dsn


def paired_comparison(cases):
    paired = [
        row
        for row in cases
        if row.get("agent")
        and row["baseline"]["recommendation_legal"]
        and row["agent"]["recommendation_legal"]
    ]
    same_mode = [
        row
        for row in paired
        if row["baseline"]["recommended"]["delivery_mode"]
        == row["agent"]["recommended"]["delivery_mode"]
    ]
    return {
        "both_solved_cases": len(paired),
        "same_delivery_mode_cases": len(same_mode),
        "same_mode_cost_differences_cents": [
            {
                "case_id": row["case_id"],
                "agent_minus_baseline": row["agent"]["transfer_cost_cents"]
                - row["baseline"]["transfer_cost_cents"],
            }
            for row in same_mode
        ],
        "completion_differences_days": [
            {
                "case_id": row["case_id"],
                "agent_minus_baseline": row["agent"]["completion_days"]
                - row["baseline"]["completion_days"],
            }
            for row in paired
        ],
    }


def run(
    suite: str,
    output: Path,
    *,
    live=False,
    prices=None,
    cost_unit="unspecified",
    cache_price=None,
    case_ids=None,
):
    cases, corpus_hash = load_cases(suite)
    if case_ids:
        if suite != "pilot" or not set(case_ids).issubset(
            {case["id"] for case in cases}
        ):
            raise ValueError("定向调试仅允许 pilot 中的案例，保留集不可筛选")
        cases = [case for case in cases if case["id"] in set(case_ids)]
    cfg = settings()
    # 真实模型费用必须显式开启；此前用户暂缓配置时，只运行 baseline，报告保留 NOT_RUN。
    if live and (
        not cfg.openai_api_key.get_secret_value() or cfg.llm_provider == "scripted-test"
    ):
        raise ValueError(
            "真实比较需要项目模型密钥和非 scripted-test 供应商；不得用替身填效果数据"
        )
    configuration = model_configuration() if live else None
    dsn = prepare_test_database() if live else None
    report = {
        "run_id": str(uuid4()),
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "suite": suite,
        "corpus_hash": corpus_hash,
        "baseline_version": baseline.BASELINE_VERSION,
        "prompt_hash": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "schema_hash": digest(AgentDecision.model_json_schema()),
        "effective_system_prompt_hash": hashlib.sha256(
            (
                SYSTEM_PROMPT
                + json.dumps(
                    AgentDecision.model_json_schema(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ).encode()
        ).hexdigest(),
        "tools_hash": digest(TOOL_CATALOG),
        "agent_status": "RUNNING" if live else "NOT_RUN",
        "model_configuration": configuration,
        "model_parameters": {
            "temperature": 0,
            "response_format": "json_object",
            "max_calls": cfg.model_max_calls,
            "input_budget": cfg.model_input_budget,
            "output_budget": cfg.model_output_budget,
            "output_per_call": cfg.model_output_per_call,
            "timeout_seconds": cfg.model_timeout_seconds,
        }
        if live
        else None,
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("langgraph", "langchain", "langchain-openai", "pydantic")
        },
        "real_model_comparison_executed": False,
        "prices_per_million_tokens": prices,
        "cached_input_price_per_million": cache_price,
        "pricing_scope": "Published token list-price estimate, excludes free quota/promotions; unknown usage is not zero cost",
        "cost_unit": cost_unit,
        "timing_scope": "synthetic in-process read tools; baseline CPU; agent production graph+PostgreSQL+provider; no ERP network latency",
        "scope": "decision quality until approval; no approvals, business writes or crash tests",
        "cases": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        completed_agents = [
            row["agent"] for row in report["cases"] if row.get("agent") is not None
        ]
        report["completed_cases"] = len(report["cases"])
        report["expected_cases"] = len(cases)
        report["pending_case_ids"] = [
            case["id"]
            for case in cases
            if case["id"] not in {row["case_id"] for row in report["cases"]}
        ]
        report["real_model_comparison_executed"] = live and any(
            row["model_calls"] > 0 for row in completed_agents
        )
        report["baseline_summary"] = aggregate(
            [row["baseline"] for row in report["cases"]]
        )
        report["agent_summary"] = aggregate(completed_agents) if live else None
        report["paired_comparison"] = (
            paired_comparison(report["cases"]) if live else None
        )
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def evaluate(saver=None):
        for case in cases:
            anchor = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            context = materialize(case, anchor)
            truth = oracle(context)
            row = {
                "case_id": case["id"],
                "family": case["family"],
                "input_hash": digest(context),
                "context": context,
                "oracle": truth,
                "baseline": evaluate_baseline(context, truth),
                "agent": None,
            }
            if live:
                row["agent"] = evaluate_agent(
                    context,
                    truth,
                    saver,
                    configuration,
                    prices,
                    cache_price=cache_price,
                )
            report["cases"].append(row)
            save()

    save()
    try:
        if live:
            assert dsn, "live 评测必须先准备 _test 数据库连接串"
            with PostgresSaver.from_conn_string(dsn) as saver:
                saver.setup()
                evaluate(saver)
            report["agent_status"] = "COMPARISON_RECORDED"
        else:
            evaluate()
    except BaseException as exc:
        report["agent_status"] = (
            "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "RUN_FAILED"
        )
        report["error_type"] = type(exc).__name__
        save()
        raise
    save()
    return report
