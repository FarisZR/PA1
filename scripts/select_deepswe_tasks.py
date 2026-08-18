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
   harnesses by repricing the public DeepSWE raw token/cache usage with a frozen
   PA1 pricing table. Opus and Luna use per-request trajectory metrics where
   provider billing depends on cache writes or context length.

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
        "cost_method": "anthropic_trajectory",
    },
    {
        "label": "GPT-5.6 Luna max",
        "model": "gpt-5-6-luna",
        "reasoning_effort": "max",
        "cost_method": "openai_trajectory",
    },
    {
        "label": "DeepSeek V4 Flash max (Fireworks)",
        "model": "deepseek-v4-flash",
        "reasoning_effort": "max",
        "cost_method": "fireworks_tokens",
        "pricing_key": "fireworks_v4_flash",
    },
    {
        "label": "Kimi K3 max (Fireworks)",
        "model": "kimi-k3",
        "reasoning_effort": "max",
        "cost_method": "fireworks_tokens",
        "pricing_key": "fireworks_kimi_k3",
    },
)

# Frozen public prices used for this budget projection. Values are USD/MTok.
PRICING = {
    "anthropic_opus_5": {
        "input": 5.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cached_input": 0.50,
        "output": 25.0,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "openai_gpt_5_6_luna": {
        # Current post-2026-07-30 standard API rates. Cached reads keep the
        # 90% discount. GPT-5.6 cache writes are 1.25x uncached input.
        "input": 0.20,
        "cached_input": 0.02,
        "cache_write": 0.25,
        "output": 1.20,
        "long_context_threshold": 272_000,
        "long_context_input_multiplier": 2.0,
        "long_context_output_multiplier": 1.5,
        "source": "https://developers.openai.com/api/docs/models",
        "pricing_change_source": (
            "https://openai.com/index/advancing-the-price-performance-frontier-with-gpt-5-6/"
        ),
        "cache_rules_source": "https://developers.openai.com/api/docs/guides/latest-model",
    },
    "fireworks_v4_flash": {
        "input": 0.14,
        "cached_input": 0.028,
        "output": 0.28,
        "source": "https://fireworks.ai/models/fireworks/deepseek-v4-flash",
    },
    "fireworks_kimi_k3": {
        "input": 3.0,
        "cached_input": 0.30,
        "output": 15.0,
        "source": "https://fireworks.ai/models/fireworks/kimi-k3",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--tasks-json", type=Path)
    parser.add_argument("--trials-json", type=Path)
    parser.add_argument("--release-json", type=Path)
    parser.add_argument(
        "--trajectory-cache-dir",
        type=Path,
        default=Path(".cache/deepswe-trajectories"),
        help="Local cache for public DeepSWE trajectory JSON files.",
    )
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


class TrajectoryStore:
    """Fetch and cache immutable DeepSWE trajectory artifacts used for pricing."""

    def __init__(self, release: dict[str, Any], cache_dir: Path):
        self.release = release
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._docs: dict[str, dict[str, Any]] = {}
        self.sha256_by_trial: dict[str, str] = {}

    def url_for(self, trial_name: str) -> str:
        base = self.release["artifact_base_url"].rstrip("/")
        pattern = self.release["artifact_patterns"]["trajectory"]
        path = pattern.format(trial_name=trial_name).lstrip("/")
        return f"{base}/{path}"

    def get(self, trial_name: str) -> dict[str, Any]:
        if trial_name in self._docs:
            return self._docs[trial_name]

        path = self.cache_dir / f"{trial_name}.json"
        if path.exists():
            raw = path.read_bytes()
        else:
            request = urllib.request.Request(
                self.url_for(trial_name), headers={"User-Agent": "PA1-DeepSWE-selection/1"}
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read()
            path.write_bytes(raw)

        doc = json.loads(raw)
        self._docs[trial_name] = doc
        self.sha256_by_trial[trial_name] = hashlib.sha256(raw).hexdigest()
        return doc


def _step_prompt_details(metrics: dict[str, Any]) -> dict[str, Any]:
    return ((metrics.get("extra") or {}).get("prompt_tokens_details") or {})


def anthropic_opus5_cost(trial: dict[str, Any], store: TrajectoryStore) -> float:
    """Reprice an Opus 5 rollout from per-request cache read/write usage."""
    prices = PRICING["anthropic_opus_5"]
    trajectory = store.get(trial["trial_name"])
    total = 0.0

    for step in trajectory["steps"]:
        metrics = step.get("metrics") or {}
        if metrics.get("prompt_tokens") is None:
            continue
        prompt_tokens = int(metrics.get("prompt_tokens") or 0)
        output_tokens = int(metrics.get("completion_tokens") or 0)
        details = _step_prompt_details(metrics)
        cached_value = details.get("cached_tokens")
        cached_tokens = int(
            cached_value if cached_value is not None else (metrics.get("cached_tokens") or 0)
        )

        creation = details.get("cache_creation_token_details") or {}
        write_5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
        write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
        creation_total = int(details.get("cache_creation_tokens") or 0)
        if write_5m + write_1h == 0 and creation_total:
            # DeepSWE Opus 5 trajectories currently expose the TTL breakdown.
            # Keep a deterministic fallback if an older artifact only has the total.
            write_5m = creation_total

        base_input = max(0, prompt_tokens - cached_tokens - write_5m - write_1h)
        total += (
            base_input * prices["input"]
            + write_5m * prices["cache_write_5m"]
            + write_1h * prices["cache_write_1h"]
            + cached_tokens * prices["cached_input"]
            + output_tokens * prices["output"]
        ) / 1_000_000.0

    return total


def openai_luna_cost_range(
    trial: dict[str, Any], store: TrajectoryStore
) -> tuple[float, float]:
    """Return observed-usage cost and cache-write upper bound for GPT-5.6 Luna.

    DeepSWE's historical GPT-5.6 trajectories expose cached read tokens but do
    not expose the newer ``cache_write_tokens`` field. The first value therefore
    prices unlabelled uncached tokens at the normal input rate. The second is a
    conservative bound that treats every unlabelled uncached token as a cache
    write (1.25x input). Long-context multipliers are applied per request.
    """
    prices = PRICING["openai_gpt_5_6_luna"]
    trajectory = store.get(trial["trial_name"])
    observed_total = 0.0
    upper_total = 0.0

    for step in trajectory["steps"]:
        metrics = step.get("metrics") or {}
        if metrics.get("prompt_tokens") is None:
            continue
        prompt_tokens = int(metrics.get("prompt_tokens") or 0)
        output_tokens = int(metrics.get("completion_tokens") or 0)
        details = _step_prompt_details(metrics)
        cached_value = details.get("cached_tokens")
        cached_tokens = int(
            cached_value if cached_value is not None else (metrics.get("cached_tokens") or 0)
        )
        uncached_tokens = max(0, prompt_tokens - cached_tokens)

        cache_write_value = details.get("cache_write_tokens")
        cache_write_known = cache_write_value is not None
        cache_write_tokens = int(cache_write_value or 0)
        normal_input_tokens = max(0, uncached_tokens - cache_write_tokens)

        input_multiplier = 1.0
        output_multiplier = 1.0
        if prompt_tokens > int(prices["long_context_threshold"]):
            input_multiplier = float(prices["long_context_input_multiplier"])
            output_multiplier = float(prices["long_context_output_multiplier"])

        observed_total += (
            normal_input_tokens * prices["input"] * input_multiplier
            + cache_write_tokens * prices["cache_write"] * input_multiplier
            + cached_tokens * prices["cached_input"] * input_multiplier
            + output_tokens * prices["output"] * output_multiplier
        ) / 1_000_000.0

        if cache_write_known:
            upper_total += (
                normal_input_tokens * prices["input"] * input_multiplier
                + cache_write_tokens * prices["cache_write"] * input_multiplier
                + cached_tokens * prices["cached_input"] * input_multiplier
                + output_tokens * prices["output"] * output_multiplier
            ) / 1_000_000.0
        else:
            upper_total += (
                uncached_tokens * prices["cache_write"] * input_multiplier
                + cached_tokens * prices["cached_input"] * input_multiplier
                + output_tokens * prices["output"] * output_multiplier
            ) / 1_000_000.0

    return observed_total, upper_total


def fireworks_cost(trial: dict[str, Any], pricing_key: str) -> float | None:
    input_tokens = trial.get("n_input_tokens")
    cached_tokens = trial.get("n_cache_tokens")
    output_tokens = trial.get("n_output_tokens")
    if input_tokens is None or cached_tokens is None or output_tokens is None:
        return None
    uncached_tokens = max(0, input_tokens - cached_tokens)
    prices = PRICING[pricing_key]
    return (
        uncached_tokens * prices["input"]
        + cached_tokens * prices["cached_input"]
        + output_tokens * prices["output"]
    ) / 1_000_000.0


def target_trial_cost_range(
    trial: dict[str, Any], config: dict[str, Any], store: TrajectoryStore
) -> tuple[float, float] | None:
    method = config["cost_method"]
    if method == "anthropic_trajectory":
        value = anthropic_opus5_cost(trial, store)
        return value, value
    if method == "openai_trajectory":
        return openai_luna_cost_range(trial, store)
    if method == "fireworks_tokens":
        value = fireworks_cost(trial, config["pricing_key"])
        return None if value is None else (value, value)
    raise ValueError(f"Unknown cost method: {method}")


def estimate_costs(
    selected: list[dict[str, Any]],
    trials: list[dict[str, Any]],
    harnesses: int,
    store: TrajectoryStore,
) -> dict[str, Any]:
    selected_ids = {row["task_id"] for row in selected}
    model_results: list[dict[str, Any]] = []
    grand_total = 0.0
    grand_total_upper = 0.0

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
        one_harness_upper = 0.0
        for selected_task in selected:
            task_id = selected_task["task_id"]
            rows = [trial for trial in matching if trial["task_name"] == task_id]
            ranges = [
                result
                for trial in rows
                if (result := target_trial_cost_range(trial, config, store)) is not None
            ]
            if not ranges:
                raise RuntimeError(
                    f"No cost-bearing target rollouts for {config['label']} / {task_id}"
                )
            costs = [value[0] for value in ranges]
            upper_costs = [value[1] for value in ranges]
            average_cost = statistics.mean(costs)
            average_upper = statistics.mean(upper_costs)
            one_harness_total += average_cost
            one_harness_upper += average_upper
            per_task.append(
                {
                    "task_id": task_id,
                    "n_public_rollouts": len(rows),
                    "n_cost_rollouts": len(ranges),
                    "average_cost_usd": average_cost,
                    "average_cost_upper_usd": average_upper,
                }
            )

        projected_total = one_harness_total * harnesses
        projected_upper = one_harness_upper * harnesses
        grand_total += projected_total
        grand_total_upper += projected_upper
        model_results.append(
            {
                "label": config["label"],
                "model": config["model"],
                "reasoning_effort": config["reasoning_effort"],
                "cost_method": config["cost_method"],
                "average_cost_per_selected_task_usd": one_harness_total / len(selected),
                "one_harness_total_usd": one_harness_total,
                "one_harness_total_upper_usd": one_harness_upper,
                "projected_total_usd": projected_total,
                "projected_total_upper_usd": projected_upper,
                "per_task": per_task,
            }
        )

    return {
        "harness_count": harnesses,
        "runs_per_model": len(selected) * harnesses,
        "total_projected_runs": len(selected) * harnesses * len(TARGET_CONFIGS),
        "models": model_results,
        "grand_total_usd": grand_total,
        "grand_total_upper_usd": grand_total_upper,
        "uncertainty_note": (
            "GPT-5.6 historical DeepSWE trajectories do not expose cache_write_tokens. "
            "The upper bound treats every unlabelled uncached GPT input token as a "
            "cache write at 1.25x input pricing; other models are repriced exactly from "
            "the available raw cache/token fields."
        ),
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
        upper = model["projected_total_upper_usd"]
        projected = model["projected_total_usd"]
        suffix = f"-${upper:.2f}" if upper > projected + 1e-9 else ""
        print(
            f"  {model['label']:38s} "
            f"1 harness=${model['one_harness_total_usd']:.2f}  "
            f"{costs['harness_count']} harnesses=${projected:.2f}{suffix}"
        )
    total = costs["grand_total_usd"]
    upper = costs["grand_total_upper_usd"]
    if upper > total + 1e-9:
        print(f"\n  GRAND TOTAL: ${total:.2f}-${upper:.2f}")
    else:
        print(f"\n  GRAND TOTAL: ${total:.2f}")
    print(f"  Projected runs: {costs['total_projected_runs']}")


def main() -> int:
    args = parse_args()
    if args.harnesses < 1:
        raise SystemExit("--harnesses must be >= 1")

    tasks_url = f"{args.base_url}/{args.version}/tasks.json"
    trials_url = f"{args.base_url}/{args.version}/trials.json"
    release_url = f"{args.base_url}/{args.version}/release.json"
    tasks_doc, tasks_raw, tasks_source = load_json_bytes(args.tasks_json, tasks_url)
    trials_doc, trials_raw, trials_source = load_json_bytes(args.trials_json, trials_url)
    release_doc, release_raw, release_source = load_json_bytes(args.release_json, release_url)

    tasks = tasks_doc["rows"]
    trials = trials_doc["rows"]
    metrics = build_task_metrics(tasks, trials)
    selected, strata_audit = select_tasks(tasks, metrics)
    trajectory_store = TrajectoryStore(release_doc, args.trajectory_cache_dir)
    cost_estimate = estimate_costs(selected, trials, args.harnesses, trajectory_store)

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
            "release": {
                "source": release_source,
                "sha256": hashlib.sha256(release_raw).hexdigest(),
            },
            "pricing": PRICING,
            "cost_trajectory_artifacts": {
                "count": len(trajectory_store.sha256_by_trial),
                "sha256_by_trial": dict(sorted(trajectory_store.sha256_by_trial.items())),
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
