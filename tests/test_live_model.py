"""Opt-in PAID/provider tests. Default CI must never run these implicitly."""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from order_agent.config import settings
from order_agent.integrations.investigation import TOOL_CATALOG
from order_agent.llm.decision_model import SYSTEM_PROMPT

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_LLM") != "1",
        reason="Real provider calls require explicit RUN_LIVE_LLM=1",
    ),
]


@pytest.mark.parametrize(
    "profile,run",
    [(profile, run) for profile in ("A", "B", "C", "D", "E", "F") for run in range(2)],
)
def test_real_model_resource_scenarios_and_recovery(live, profile, run):
    cfg = settings()
    assert cfg.openai_api_key.get_secret_value(), (
        "Configure the project's OPENAI_API_KEY before opting in"
    )
    assert cfg.llm_provider != "scripted-test", (
        "Scripted responses cannot count as live evaluation"
    )
    previous = dict(live.env)
    live.env.update(
        PLANNER_MODE="llm",
        LLM_PROVIDER=cfg.llm_provider,
        LLM_MODEL=cfg.llm_model,
        OPENAI_BASE_URL=cfg.openai_base_url,
        OPENAI_API_KEY=cfg.openai_api_key.get_secret_value(),
    )
    live.request_timeout = 420
    record = {
        "evaluation_run_id": os.environ.get("EVALUATION_RUN_ID", "manual-opt-in"),
        "profile": profile,
        "run": run,
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "provider": cfg.llm_provider,
        "model": cfg.llm_model,
        "base_url": cfg.openai_base_url,
        "model_parameters": {
            "max_calls": cfg.model_max_calls,
            "input_budget": cfg.model_input_budget,
            "output_budget": cfg.model_output_budget,
            "output_per_call": cfg.model_output_per_call,
            "timeout_seconds": cfg.model_timeout_seconds,
        },
        "prompt_hash": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "tools_hash": hashlib.sha256(
            json.dumps(TOOL_CATALOG, sort_keys=True).encode()
        ).hexdigest(),
    }
    output = Path(__file__).resolve().parents[1] / "docs" / "live-model-evaluation.json"
    try:
        live.restart_agent()
        fault = "crash_after_apply" if profile == "B" and run == 0 else "none"
        task = live.task(profile=profile, fault=fault)
        record.update(
            task_id=task["id"],
            status=task["status"],
            history=task["state"].get("decision_history", []),
            proposal=task["state"].get("adaptive_proposal"),
            validation=task["state"].get("validation"),
            model_calls=task["model_calls"],
            error=task["state"].get("error"),
            context=task["state"].get("context"),
        )
        assert task["state"]["decision_protocol"] == "v2"
        assert task["model_calls"]
        if profile in {"C", "E"}:
            assert task["status"] == "NEEDS_MANUAL"
            assert task["state"]["decision_history"][-1]["action"] == "manual", (
                "预算耗尽不能算成模型识别了不可满足场景"
            )
        else:
            assert task["status"] == "WAITING_APPROVAL", (
                "A feasible case was not solved; retain this as a quality failure"
            )
            if profile == "F":
                resource = task["state"]["context"]["warehouses"][0]
                live.request(
                    "POST",
                    f"/api/demo/orders/{task['order_id']}/resources",
                    body={
                        "target": "route",
                        "resource_id": resource["id"],
                        "unit_cost_cents": resource["unit_cost_cents"] + 1,
                    },
                )
                selected = task["state"]["validation"]["valid_ids"][0]
                stale = live.request(
                    "POST",
                    f"/api/tasks/{task['id']}/approval",
                    body={
                        **task["approval_context"][selected],
                        "proposal_version": task["state"]["revision"],
                        "decision": "approve",
                        "candidate_id": selected,
                        "request_id": str(uuid4()),
                    },
                )
                assert stale["status"] == "STALE"
                task = live.request("POST", f"/api/tasks/{task['id']}/replan")
                assert task["status"] == "WAITING_APPROVAL"
                record["resource_change_reapproval"] = True
                record["replanned_history"] = task["state"]["decision_history"]
                record["replanned_model_calls"] = task["model_calls"]
            calls_before = len(task["model_calls"])
            if profile == "A" and run == 0:
                checkpoint = task["checkpoint_id"]
                old_pid = live.agent_server_pid
                live.restart_agent()
                assert live.agent_server_pid != old_pid
                restored = live.request("GET", f"/api/tasks/{task['id']}")
                assert restored["checkpoint_id"] == checkpoint
                record["approval_wait_process_recovery"] = True
            body = {
                **task["approval_context"][task["state"]["validation"]["valid_ids"][0]],
                "proposal_version": task["state"]["revision"],
                "decision": "approve",
                "candidate_id": task["state"]["validation"]["valid_ids"][0],
                "request_id": str(uuid4()),
            }
            try:
                done = live.request(
                    "POST", f"/api/tasks/{task['id']}/approval", body=body
                )
                assert fault == "none", (
                    "Expected the actual crash fault to terminate the process"
                )
            except httpx.TransportError:
                assert fault == "crash_after_apply"
                live.agent.wait(timeout=10)
                assert live.agent.returncode == 86
                live.restart_agent()
                done = live.request("POST", f"/api/tasks/{task['id']}/recover")
                assert done["state"]["execution"]["replayed"] is True
                record["business_commit_process_recovery"] = True
            assert done["status"] == "COMPLETED"
            assert len(done["model_calls"]) == calls_before
            record["execution_status"] = done["status"]
        record["outcome"] = "passed"
    except Exception as exc:
        record["outcome"] = "failed"
        record["error_type"] = type(exc).__name__
        raise
    finally:
        # Never include SDK exceptions, credentials or connection strings in the report.
        previous_report = (
            json.loads(output.read_text(encoding="utf-8")) if output.exists() else []
        )
        output.write_text(
            json.dumps(
                previous_report + [record], ensure_ascii=False, indent=2, default=str
            ),
            encoding="utf-8",
        )
        live.env = previous
        live.request_timeout = 40
        live.restart_agent()
