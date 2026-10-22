"""冻结实现后验收，重复同一批30例；不依据保留集结果修改决策规则。"""

import hashlib
import importlib.metadata
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from order_agent.config import settings
from order_agent.evaluation.harness import run
from order_agent.llm.decision_model import model_configuration


def manifest(root):
    cfg = settings()
    paths = [
        *root.joinpath("src").rglob("*.py"),
        *root.joinpath("src").rglob("*.sql"),
        root / "src/order_agent/evaluation/cases.json",
        *root.joinpath("scripts").glob("*.py"),
        root / "uv.lock",
        root / "pyproject.toml",
    ]
    return {
        "source_hashes": {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for path in sorted(paths)
        },
        "model": model_configuration(),
        "parameters": {
            key: getattr(cfg, key)
            for key in (
                "model_max_calls",
                "model_input_budget",
                "model_output_budget",
                "model_output_per_call",
                "model_timeout_seconds",
            )
        },
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("langgraph", "langchain-openai", "openai", "httpx", "httpx2")
        },
    }


def check_frozen(root, frozen):
    settings.cache_clear()
    if manifest(root) != frozen:
        raise ValueError("保留集期间源码、模型或预算发生变化，不得继续合并验收结果")


def compare_rounds(reports):
    expected = {row["case_id"] for report in reports for row in report["cases"]}
    by_round = [
        {row["case_id"]: row["agent"] for row in report["cases"]} for report in reports
    ]
    paired = [key for key in sorted(expected) if all(key in rows for rows in by_round)]
    outcomes = [
        {
            "case_id": key,
            "correct_by_round": [
                bool(rows[key]["overall_correct"]) for rows in by_round
            ],
            "status_by_round": [rows[key]["status"] for rows in by_round],
        }
        for key in paired
    ]
    scores = [row["agent"] for report in reports for row in report["cases"]]
    calls = [
        call
        for report in reports
        for row in report["cases"]
        for call in row["agent"].get("model_call_records", [])
    ]
    known_calls = [
        call
        for call in calls
        if call.get("actual_input") is not None
        and call.get("actual_output") is not None
    ]
    complete = bool(scores) and all(
        row["token_cost_from_supplied_prices"] is not None for row in scores
    )
    return {
        "rounds": len(reports),
        "paired_cases": len(paired),
        "correct_in_all_rounds": sum(all(row["correct_by_round"]) for row in outcomes),
        "correct_in_some_rounds_only": sum(
            any(row["correct_by_round"]) and not all(row["correct_by_round"])
            for row in outcomes
        ),
        "failed_in_all_rounds": sum(
            not any(row["correct_by_round"]) for row in outcomes
        ),
        "outcomes": outcomes,
        "mean_model_calls": sum(row["model_calls"] for row in scores) / len(scores)
        if scores
        else None,
        "usage_complete_case_attempts": sum(row["usage_complete"] for row in scores),
        "total_case_attempts": len(scores),
        "model_request_attempts": len(calls),
        "model_request_attempts_with_known_usage": len(known_calls),
        "mean_known_model_request_cost_cny": sum(
            row["known_token_cost_estimate"] or 0 for row in scores
        )
        / len(known_calls)
        if known_calls
        else None,
        "mean_token_list_price_estimate_cny": sum(
            row["token_cost_from_supplied_prices"] for row in scores
        )
        / len(scores)
        if complete
        else None,
        "mean_known_token_cost_component_cny": sum(
            row["known_token_cost_estimate"] or 0 for row in scores
        )
        / len(scores)
        if scores
        else None,
        "unknown_attempt_costs_are_zero": False,
    }


def accept(root: Path, destination: Path, rounds=2):
    cfg = settings()
    if (
        cfg.llm_provider != "dashscope"
        or cfg.llm_model != "qwen-plus"
        or cfg.llm_enable_thinking
        or cfg.model_input_budget > 128000
        or cfg.openai_base_url.rstrip("/")
        != "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ):
        raise ValueError(
            "本验收入口的价格快照仅适用于北京 qwen-plus 非思考 <=128K；其他配置需单独核实定价"
        )
    # 首次进入保留集之前必须提交源码；文档和结果可在冻结后落盘。
    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "src",
            "scripts",
            "pyproject.toml",
            "uv.lock",
        ],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    if dirty.strip():
        raise ValueError("请先提交已验证的实现，再冻结并运行保留集")
    destination.mkdir(parents=True, exist_ok=False)
    frozen = manifest(root)
    meta = {
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "manifest": frozen,
        "planned_cases_per_round": 30,
        "planned_rounds": rounds,
        "prices": {
            "currency": "CNY",
            "input_per_million": 0.8,
            "output_per_million": 2,
            "cached_input_per_million": 0.16,
            "source": "https://help.aliyun.com/zh/model-studio/qwen-plus",
            "scope": "Beijing qwen-plus nonthinking <=128K published list prices; not an invoice",
        },
    }
    (destination / "freeze.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    reports = []
    for index in range(rounds):
        check_frozen(root, frozen)
        report = run(
            "holdout",
            destination / f"round-{index + 1}.json",
            live=True,
            prices=(0.8, 2),
            cache_price=0.16,
            cost_unit="CNY",
        )
        reports.append(report)
        check_frozen(root, frozen)
        summary = {
            "state": "RUNNING" if index + 1 < rounds else "MEASURED",
            "round_summaries": [r["agent_summary"] for r in reports],
            "stability": compare_rounds(reports),
            "source_unchanged": True,
            "scope": "fixed synthetic decision evaluation until approval; no business writes or ERP",
            "limits": [
                "Same known data families, not independent enterprise sample",
                "qwen-plus is a mutable provider alias",
                "Published token estimate excludes quota, promotions and unknown usage",
            ],
        }
        (destination / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return summary
