"""Explicit opt-in entry point; records live model calls separately from offline CI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from order_agent.config import settings


def main():
    if not settings().openai_api_key.get_secret_value():
        raise SystemExit(
            "未配置项目模型密钥，真实模型验收未执行。请在忽略的 .env 中配置后再运行。"
        )
    if settings().llm_provider == "scripted-test":
        raise SystemExit("脚本化供应商不能计入真实模型验收")
    root = Path(__file__).resolve().parents[1]
    evaluation_run_id = str(uuid4())
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_live_model.py"],
        cwd=root,
        env={
            **os.environ,
            "RUN_LIVE_LLM": "1",
            "PYTHONUTF8": "1",
            "EVALUATION_RUN_ID": evaluation_run_id,
        },
    )
    report = root / "docs" / "live-model-evaluation.json"
    records = json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
    current = [
        row for row in records if row.get("evaluation_run_id") == evaluation_run_id
    ]
    summary = {
        "evaluation_run_id": evaluation_run_id,
        "expected_cases": 12,
        "recorded_cases": len(current),
        "passed": sum(row["outcome"] == "passed" for row in current),
        "failed": sum(row["outcome"] == "failed" for row in current),
        "missing_cases": max(0, 12 - len(current)),
        "exit_code": result.returncode,
    }
    (root / "docs" / "live-model-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
