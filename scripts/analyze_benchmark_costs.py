#!/usr/bin/env python3
"""Aggregate normalized model costs from Pier benchmark result files.

The result files store total prompt, cached-prompt, and completion token counts
for each trial.  This report applies the model prices from the benchmark
pricing policy to the canonical attempt of every trial. ``--include-retries``
adds the non-canonical attempts under ``.retry-attempts`` (experimental
overhead).

For the Kimi K3 run:
    python3 scripts/analyze_benchmark_costs.py data/benchmark-results/kimi-k3
    python3 scripts/analyze_benchmark_costs.py data/benchmark-results/kimi-k3 \
        --include-retries
    python3 scripts/analyze_benchmark_costs.py data/benchmark-results/kimi-k3 --format json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

# USD per 1M tokens, from benchmark/pricing.yaml for kimi-k3.
INPUT_RATE = 3.0
CACHED_INPUT_RATE = 0.3
OUTPUT_RATE = 15.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "runs_root", type=Path, help="A job directory containing Pier result.json files."
    )
    parser.add_argument(
        "--include-retries",
        action="store_true",
        help="Also count the non-canonical attempts below .retry-attempts.",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format."
    )
    return parser.parse_args()


def result_paths(root: Path, include_retries: bool) -> list[Path]:
    paths = sorted(root.glob("*/result.json"))
    if include_retries:
        paths.extend(sorted(root.glob(".retry-attempts/*/attempt-*/result.json")))
    return paths


def normalized_cost(agent_result: dict[str, Any]) -> float:
    prompt = float(agent_result.get("n_input_tokens") or 0)
    cached = float(agent_result.get("n_cache_tokens") or 0)
    output = float(agent_result.get("n_output_tokens") or 0)
    uncached = prompt - cached
    return (
        uncached * INPUT_RATE + cached * CACHED_INPUT_RATE + output * OUTPUT_RATE
    ) / 1_000_000


def load_rows(root: Path, include_retries: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in result_paths(root, include_retries):
        data = json.loads(path.read_text())
        config = data.get("config") or {}
        agent = config.get("agent") or {}
        agent_result = data.get("agent_result") or {}
        if not agent_result:
            continue
        trial = path.parent
        retry = ".retry-attempts" in path.parts
        task = data.get("task_name") or trial.name.split("__", 1)[0]
        rows.append(
            {
                "task": task.split("/", 1)[-1],
                "agent": agent.get("name"),
                "trial": trial.name,
                "retry": retry,
                "input_tokens": agent_result.get("n_input_tokens", 0),
                "cached_tokens": agent_result.get("n_cache_tokens", 0),
                "output_tokens": agent_result.get("n_output_tokens", 0),
                "cost_usd": normalized_cost(agent_result),
            }
        )
    return rows


def report(rows: list[dict[str, Any]], include_retries: bool) -> dict[str, Any]:
    by_agent: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"attempts": 0, "retry_attempts": 0, "cost_usd": 0.0}
    )
    by_task: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        agent = row["agent"]
        by_agent[agent]["attempts"] += 1
        by_agent[agent]["retry_attempts"] += int(row["retry"])
        by_agent[agent]["cost_usd"] += row["cost_usd"]
        by_task[row["task"]][agent] += row["cost_usd"]

    agents = sorted(by_agent)
    tasks = sorted(by_task)
    return {
        "pricing": {
            "input_usd_per_million": INPUT_RATE,
            "cached_input_usd_per_million": CACHED_INPUT_RATE,
            "output_usd_per_million": OUTPUT_RATE,
        },
        "include_retries": include_retries,
        "attempts": len(rows),
        "agents": {
            agent: {
                "attempts": by_agent[agent]["attempts"],
                "retry_attempts": by_agent[agent]["retry_attempts"],
                "cost_usd": by_agent[agent]["cost_usd"],
            }
            for agent in agents
        },
        "by_task": {
            task: {agent: by_task[task].get(agent, 0.0) for agent in agents}
            for task in tasks
        },
    }


def print_text(result: dict[str, Any]) -> None:
    p = result["pricing"]
    print(
        "Pricing: ${input_usd_per_million:.2f}/M input, "
        "${cached_input_usd_per_million:.2f}/M cached input, "
        "${output_usd_per_million:.2f}/M output".format(**p)
    )
    print(f"Retry attempts included: {result['include_retries']}")
    print()

    agents = list(result["agents"])
    print("Harness totals")
    print("  " + "  ".join(f"{agent:>14}" for agent in agents))
    print("  " + "  ".join(f"${result['agents'][agent]['cost_usd']:>13,.6f}" for agent in agents))
    print("  " + "  ".join(f"({result['agents'][agent]['attempts']} attempts)".rjust(14) for agent in agents))
    print()

    print("Cost by task (USD)")
    print(f"{'task':<45}" + "".join(f"{agent:>16}" for agent in agents))
    for task, costs in result["by_task"].items():
        print(f"{task:<45}" + "".join(f"${costs[agent]:>15,.6f}" for agent in agents))


def main() -> None:
    args = parse_args()
    rows = load_rows(args.runs_root, args.include_retries)
    if not rows:
        raise SystemExit(f"No result files with agent usage found under {args.runs_root}")
    result = report(rows, args.include_retries)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print_text(result)


if __name__ == "__main__":
    main()
