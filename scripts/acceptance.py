"""Run real-process acceptance and retain a machine-readable result."""

import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    report = runtime / "acceptance.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "not live_llm",
            f"--junitxml={report}",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PLANNER_MODE": "demo",
            "RUN_LIVE_LLM": "0",
        },
    )
    cases = []
    if report.exists():
        for case in ET.parse(report).iter("testcase"):
            outcome = "passed"
            for kind in ("failure", "error", "skipped"):
                if case.find(kind) is not None:
                    outcome = kind
            cases.append(
                {
                    "name": case.attrib["name"],
                    "outcome": outcome,
                    "seconds": float(case.attrib.get("time", 0)),
                }
            )
    import psycopg

    from order_agent.config import settings

    with psycopg.connect(settings().database_url.get_secret_value()) as conn:
        pg_version = conn.execute("SELECT version()").fetchone()[0]
    evidence = {
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "exit_code": result.returncode,
        "python": platform.python_version(),
        "platform": platform.system(),
        "postgresql": pg_version,
        "planner_mode": "offline-demo-and-scripted-provider",
        "real_llm": False,
        "live_llm_acceptance": "not_run",
        "real_postgresql": True,
        "database_scope": "isolated database whose name ends in _test",
        "real_process_kill": all(
            any(c["name"] == name and c["outcome"] == "passed" for c in cases)
            for name in (
                "test_real_postgres_approval_survives_process_kill",
                "test_committed_external_write_survives_agent_crash_without_duplicate",
            )
        ),
        "recovery_boundaries": {
            name: any(
                case["name"] == name and case["outcome"] == "passed" for case in cases
            )
            for name in (
                "test_approval_receipt_survives_exit_before_graph_resume",
                "test_adaptive_timeout_reconciles_same_operation_without_more_model_calls",
                "test_model_result_replayed_after_process_exit_without_second_call",
                "test_adaptive_write_commit_crash_reconciles_without_regeneration",
                "test_pending_writes_reuse_successful_parallel_queries",
                "test_concurrent_approvals_serialize_and_replay_without_duplicate_write",
            )
        },
        "review_fix_boundaries": {
            name: any(
                case["name"] == name and case["outcome"] == "passed" for case in cases
            )
            for name in (
                "test_approval_wait_does_not_count_as_transfer_time",
                "test_observations_and_rule_references_recompute_transfer_readiness",
                "test_business_transaction_rejects_transfer_time_lost_while_waiting",
                "test_event_cursor_cannot_overtake_an_uncommitted_writer",
                "test_deferred_task_survives_restart_and_streams_before_slow_run_finishes",
            )
        },
        "versions": {
            p: importlib.metadata.version(p)
            for p in (
                "langgraph",
                "langchain",
                "langgraph-checkpoint-postgres",
                "fastapi",
                "psycopg",
            )
        },
        "tests": cases,
    }
    output = ROOT / "docs" / "acceptance-latest.json"
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Acceptance report: {output}")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
