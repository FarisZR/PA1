#!/usr/bin/env python3
"""Deterministically select PA1 DeepSWE tasks and estimate benchmark cost.

The selection procedure is intentionally mechanical:

1. Download the public DeepSWE v1.1 task and trial artifacts.
2. Exclude errored mini-swe-agent rollouts and the four models evaluated by PA1
   from the task-selection reference panel.
3. Rank tasks independently within each programming language by solve rate.
   The hardest task receives difficulty percentile 100 and the easiest 0.
4. Define non-overlapping strata:
      hard   = [75, 100]
      medium = [50, 75)
5. Within each language/stratum, select the task with the largest median
   historical total token count (input + output) in the reference panel.
   Task ID is the deterministic final tie-breaker.
6. Estimate the cost of running the selected tasks once on each of four PA1
   harnesses from the public DeepSWE rollouts of the target model configs.

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_VERSION = "v1.1"
DEFAULT_BASE_URL = "https://deepswe.datacurve.ai/artifacts"
DEFAULT_OUTPUT = Path("data/deepswe_task_selection_v1.1.json")
REFERENCE_HARNESS = "mini-swe-agent"

# These exact models are excluded from the difficulty/token reference panel so
# the task selection does not depend on the systems evaluated by PA1.
EVALUATED_MODELS = {
    "claude-opus-5",
    "gpt-5-6-luna",
    "deepseek-v4-flash",
    "kimi-k3",
}

TARGET_CONFIGS = (
    {
        "label": "Claude Opus 5 medium",
        "model": "claude-opus-5",
        "reasoning_effort": "medium",
        "cost_method": "reported",
    },
    {
        "label": "GPT-5.6 Luna max",
        "model": "gpt-5-6-luna",
        "reasoning_effort": "max",
        "cost_method": "reported_factor",
        # DeepSWE v1.1's data UI applies this factor to Luna cost fields.
        "reported_cost_factor": 0.2,
    },
    {
        "label": "DeepSeek V4 Flash max (Fireworks)",
        "model": "deepseek-v4-flash",
        "reasoning_effort": "max",
        "cost_method": "fireworks_tokens",
    },
    {
        "label": "Kimi K3 max",
        "model": "kimi-k3",
        "reasoning_effort": "max",
        "cost_method": "reported",
    },
)

# Fireworks serverless DeepSeek-V4-Flash prices per 1M tokens used for PA1
# budgeting. The cached-input value is the exact listed price (the model page
# may display it rounded to $0.03 in some views).
FIREWORKS_V4_FLASH_PRICES_PER_MILLION = {
    "input": 0.14,
    "cached_input": 0.028,
    "output": 0.28,
}
FIREWORKS_V4_FLASH_PRICING_SOURCE = (
    "https://fireworks.ai/models/fireworks/deepseek-v4-flash"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--tasks-json", type=Path)
    parser.add_argument("--trials-json", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--harnesses",
        type=int,
        default=4,
        help="Number of PA1 harnesses used for total-cost projection (default: 4).",
    )
    return parser.parse_args()


def load_json_bytes(path: Path | None, url: str) -> tuple[dict[str, Any], bytes, str]:
    if path is not None:
        raw = path.read_bytes()
        source = str(path)
    else:
        request = urllib.request.Request(url, headers={"User-Agent": "PA1-DeepSWE-selection/1"})
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
        source = url
    return json.loads(raw), raw, source


def median(values: list[int]) -> float:
    if not values:
        raise ValueError("Cannot calculate median of an empty sequence")
    return float(statistics.median(values))


def percentile_for_rank(index: int, n_tasks: int) -> float:
    """Return rank-based difficulty percentile with hardest=100, easiest=0."""
    if n_tasks <= 1:
        return 100.0
    return 100.0 * (n_tasks - 1 - index) / (n_tasks - 1)


def is_in_stratum(percentile: float, stratum: str) -> bool:
    if stratum == "hard":
        return 75.0 <= percentile <= 100.0
    if stratum == "medium":
        return 50.0 <= percentile < 75.0
    raise ValueError(f"Unknown stratum: {stratum}")


def build_task_metrics(
    tasks: list[dict[str, Any]], trials: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    valid_trials: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token_trials: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for trial in trials:
        if trial.get("source") != "deep-swe":
            continue
        if trial.get("harness") != REFERENCE_HARNESS:
            continue
        if trial.get("model") in EVALUATED_MODELS:
            continue
        if trial.get("errored"):
            continue

        task_id = trial["task_name"]
        valid_trials[task_id].append(trial)
        if trial.get("n_input_tokens") is not None and trial.get("n_output_tokens") is not None:
            token_trials[task_id].append(trial)

    metrics: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = task["id"]
        valid = valid_trials[task_id]
        with_tokens = token_trials[task_id]
        if not valid:
            raise RuntimeError(f"No valid reference rollouts for {task_id}")
        if not with_tokens:
            raise RuntimeError(f"No token-bearing reference rollouts for {task_id}")

        metrics[task_id] = {
            "solve_rate": sum(bool(row.get("passed")) for row in valid) / len(valid),
            "n_reference_rollouts": len(valid),
            "n_token_rollouts": len(with_tokens),
            "median_input_tokens": median([row["n_input_tokens"] for row in with_tokens]),
            "median_output_tokens": median([row["n_output_tokens"] for row in with_tokens]),
            "median_total_tokens": median(
                [row["n_input_tokens"] + row["n_output_tokens"] for row in with_tokens]
            ),
        }

    return metrics


def select_tasks(
    tasks: list[dict[str, Any]], metrics: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tasks_by_language: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        tasks_by_language[task["language"]].append(task)

    selected: list[dict[str, Any]] = []
    strata_audit: dict[str, Any] = {}

    for language in sorted(tasks_by_language):
        ranked = sorted(
            tasks_by_language[language],
            key=lambda task: (metrics[task["id"]]["solve_rate"], task["id"]),
        )
        n_tasks = len(ranked)
        for index, task in enumerate(ranked):
            task_metrics = metrics[task["id"]]
            task_metrics["difficulty_rank"] = index + 1
            task_metrics["difficulty_percentile"] = percentile_for_rank(index, n_tasks)

        language_audit: dict[str, Any] = {}
        for stratum in ("hard", "medium"):
            candidates = [
                task
                for task in ranked
                if is_in_stratum(metrics[task["id"]]["difficulty_percentile"], stratum)
            ]
            if not candidates:
                raise RuntimeError(f"No {stratum} candidates for language {language}")

            # Primary factor: membership in the predefined difficulty stratum.
            # Secondary factor: largest median historical token workload.
            # Final tie-breaker: lexicographically smallest DeepSWE task ID.
            candidates_by_tokens = sorted(
                candidates,
                key=lambda task: (-metrics[task["id"]]["median_total_tokens"], task["id"]),
            )
            winner = candidates_by_tokens[0]
            winner_metrics = metrics[winner["id"]]

            selected.append(
                {
                    "stratum": stratum,
                    "language": language,
                    "task_id": winner["id"],
                    "problem_title": winner.get("problem_title"),
                    "repository": winner.get("repository"),
                    **winner_metrics,
                }
            )
            language_audit[stratum] = {
                "candidate_count": len(candidates),
                "candidate_task_ids": [task["id"] for task in candidates],
                "selected_task_id": winner["id"],
            }
        strata_audit[language] = language_audit

    selected.sort(key=lambda row: (row["stratum"], row["language"]))
    return selected, strata_audit


def target_trial_cost(trial: dict[str, Any], config: dict[str, Any]) -> float | None:
    method = config["cost_method"]
    if method == "reported":
        value = trial.get("cost_usd")
        return float(value) if value is not None else None
    if method == "reported_factor":
        value = trial.get("cost_usd")
        if value is None:
            return None
        return float(value) * float(config["reported_cost_factor"])
    if method == "fireworks_tokens":
        input_tokens = trial.get("n_input_tokens")
        cached_tokens = trial.get("n_cache_tokens")
        output_tokens = trial.get("n_output_tokens")
        if input_tokens is None or cached_tokens is None or output_tokens is None:
            return None
        uncached_tokens = max(0, input_tokens - cached_tokens)
        prices = FIREWORKS_V4_FLASH_PRICES_PER_MILLION
        return (
            uncached_tokens * prices["input"]
            + cached_tokens * prices["cached_input"]
            + output_tokens * prices["output"]
        ) / 1_000_000.0
    raise ValueError(f"Unknown cost method: {method}")


def estimate_costs(
    selected: list[dict[str, Any]], trials: list[dict[str, Any]], harnesses: int
) -> dict[str, Any]:
    selected_ids = {row["task_id"] for row in selected}
    model_results: list[dict[str, Any]] = []
    grand_total = 0.0

    for config in TARGET_CONFIGS:
        matching = [
            trial
            for trial in trials
            if trial.get("source") == "deep-swe"
            and trial.get("task_name") in selected_ids
            and trial.get("model") == config["model"]
            and trial.get("reasoning_effort") == config["reasoning_effort"]
        ]

        per_task: list[dict[str, Any]] = []
        one_harness_total = 0.0
        for selected_task in selected:
            task_id = selected_task["task_id"]
            rows = [trial for trial in matching if trial["task_name"] == task_id]
            costs = [
                cost
                for trial in rows
                if (cost := target_trial_cost(trial, config)) is not None
            ]
            if not costs:
                raise RuntimeError(
                    f"No cost-bearing target rollouts for {config['label']} / {task_id}"
                )
            average_cost = statistics.mean(costs)
            one_harness_total += average_cost
            per_task.append(
                {
                    "task_id": task_id,
                    "n_public_rollouts": len(rows),
                    "n_cost_rollouts": len(costs),
                    "average_cost_usd": average_cost,
                }
            )

        projected_total = one_harness_total * harnesses
        grand_total += projected_total
        model_results.append(
            {
                "label": config["label"],
                "model": config["model"],
                "reasoning_effort": config["reasoning_effort"],
                "cost_method": config["cost_method"],
                "average_cost_per_selected_task_usd": one_harness_total / len(selected),
                "one_harness_total_usd": one_harness_total,
                "projected_total_usd": projected_total,
                "per_task": per_task,
            }
        )

    return {
        "harness_count": harnesses,
        "runs_per_model": len(selected) * harnesses,
        "total_projected_runs": len(selected) * harnesses * len(TARGET_CONFIGS),
        "models": model_results,
        "grand_total_usd": grand_total,
    }


def print_summary(result: dict[str, Any]) -> None:
    print("Selected tasks")
    print("==============")
    for stratum in ("hard", "medium"):
        print(f"\n{stratum.upper()}")
        for row in result["selected_tasks"]:
            if row["stratum"] != stratum:
                continue
            print(
                f"  {row['language']:10s} {row['task_id']:45s} "
                f"solve={row['solve_rate'] * 100:5.1f}% "
                f"difficulty_pct={row['difficulty_percentile']:5.1f} "
                f"median_tokens={row['median_total_tokens'] / 1_000_000:6.2f}M"
            )

    costs = result["cost_estimate"]
    print("\nCost projection")
    print("===============")
    for model in costs["models"]:
        print(
            f"  {model['label']:38s} "
            f"1 harness=${model['one_harness_total_usd']:.2f}  "
            f"{costs['harness_count']} harnesses=${model['projected_total_usd']:.2f}"
        )
    print(f"\n  GRAND TOTAL: ${costs['grand_total_usd']:.2f}")
    print(f"  Projected runs: {costs['total_projected_runs']}")


def main() -> int:
    args = parse_args()
    if args.harnesses < 1:
        raise SystemExit("--harnesses must be >= 1")

    tasks_url = f"{args.base_url}/{args.version}/tasks.json"
    trials_url = f"{args.base_url}/{args.version}/trials.json"
    tasks_doc, tasks_raw, tasks_source = load_json_bytes(args.tasks_json, tasks_url)
    trials_doc, trials_raw, trials_source = load_json_bytes(args.trials_json, trials_url)

    tasks = tasks_doc["rows"]
    trials = trials_doc["rows"]
    metrics = build_task_metrics(tasks, trials)
    selected, strata_audit = select_tasks(tasks, metrics)
    cost_estimate = estimate_costs(selected, trials, args.harnesses)

    result = {
        "method": {
            "benchmark_version": args.version,
            "reference_harness": REFERENCE_HARNESS,
            "excluded_evaluated_models": sorted(EVALUATED_MODELS),
            "difficulty_metric": "solve rate over valid non-target mini-swe-agent rollouts",
            "difficulty_percentile": (
                "rank within language; hardest=100, easiest=0; solve-rate ties use task_id"
            ),
            "hard_stratum": "75 <= difficulty_percentile <= 100",
            "medium_stratum": "50 <= difficulty_percentile < 75",
            "secondary_selection_metric": (
                "maximum median (n_input_tokens + n_output_tokens) over valid non-target "
                "mini-swe-agent rollouts"
            ),
            "secondary_tie_breaker": "lexicographically smallest task_id",
        },
        "sources": {
            "tasks": {
                "source": tasks_source,
                "sha256": hashlib.sha256(tasks_raw).hexdigest(),
            },
            "trials": {
                "source": trials_source,
                "sha256": hashlib.sha256(trials_raw).hexdigest(),
            },
            "fireworks_v4_flash_pricing": {
                "source": FIREWORKS_V4_FLASH_PRICING_SOURCE,
                "usd_per_million_tokens": FIREWORKS_V4_FLASH_PRICES_PER_MILLION,
            },
        },
        "selected_tasks": selected,
        "strata_audit": strata_audit,
        "cost_estimate": cost_estimate,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print_summary(result)
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
