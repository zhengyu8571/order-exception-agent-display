"""Explicit paid holdout acceptance; freezes source/config and repeats without tuning."""

import argparse
import json
from pathlib import Path

from order_agent.evaluation.holdout import accept

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--rounds", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument("--output", type=Path, default=Path("docs/p9-holdout"))
    args = parser.parse_args()
    if not args.live:
        parser.error("必须显式 --live；该入口会实际调用模型")
    print(
        json.dumps(
            accept(Path(__file__).resolve().parents[1], args.output, args.rounds),
            ensure_ascii=False,
            indent=2,
        )
    )
