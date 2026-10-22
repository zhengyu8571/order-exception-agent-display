"""默认只评测规则基线；--live 才调用真实模型，和可靠性 acceptance 分开报告。"""

import argparse
import json
import math
from pathlib import Path

from order_agent.evaluation.harness import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["pilot", "holdout", "all"], default="pilot")
    parser.add_argument(
        "--live", action="store_true", help="显式调用付费供应商；默认不运行 Agent"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--case-ids", nargs="+", help="仅 pilot 可定向调试；保留集禁止筛选"
    )
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    parser.add_argument("--cached-input-price-per-million", type=float)
    parser.add_argument("--cost-unit", default="unspecified")
    args = parser.parse_args()
    if args.cached_input_price_per_million is not None and (
        not math.isfinite(args.cached_input_price_per_million)
        or args.cached_input_price_per_million < 0
        or args.input_price_per_million is None
        or args.output_price_per_million is None
    ):
        parser.error("缓存输入单价须为有限非负数，且必须同时提供输入/输出单价")
    pair = (args.input_price_per_million, args.output_price_per_million)
    if any(value is not None for value in pair) and (
        any(value is None for value in pair)
        or any(not math.isfinite(value) or value < 0 for value in pair)
    ):
        parser.error("输入和输出单价必须同时提供，且为有限非负数")
    output = args.output or Path("docs") / (
        "decision-evaluation-live.json"
        if args.live
        else "decision-evaluation-baseline.json"
    )
    report = run(
        args.suite,
        output,
        live=args.live,
        prices=pair if pair[0] is not None else None,
        cost_unit=args.cost_unit,
        cache_price=args.cached_input_price_per_million,
        case_ids=args.case_ids,
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "agent_status": report["agent_status"],
                "baseline": report["baseline_summary"],
                "agent": report["agent_summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
