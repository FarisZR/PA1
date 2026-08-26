#!/usr/bin/env python3
"""Build a deterministic 20-task PA1 DeepSWE selection.

This is an extension of ``select_deepswe_tasks.py`` and does not change the
primary 10-task sample. It adds 10 optional tasks while preserving the same
eligibility and ranking criteria:

- rank tasks within each language by reference-panel solve rate;
- hard = difficulty percentile [75, 100];
- medium = difficulty percentile [50, 75);
- within a language/stratum, prefer larger median historical total token count;
- task ID is the final tie-breaker.

The first optional pass takes the second-highest-token task in each
language/stratum. DeepSWE v1.1 has only five JavaScript and five Rust tasks, so
its JavaScript-medium and Rust-medium strata contain only one task each. Those
two cells therefore cannot supply a second distinct task without violating the
fixed strata. The two remaining optional slots are filled deterministically by
the highest-token eligible tasks remaining anywhere in the hard/medium strata.

The result is exactly 20 distinct tasks: the original 10 primary tasks plus 10
optional tasks. Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import select_deepswe_tasks as base


DEFAULT_OUTPUT = Path("data/deepswe_task_selection_20_v1.1.json")
OPTIONAL_TASK_COUNT = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=base.DEFAULT_VERSION)
    parser.add_argument("--base-url", default=base.DEFAULT_BASE_URL)
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
        help="Number of PA1 harnesses used for cost projection (default: 4).",
    )
    return parser.parse_args()


def ranked_eligible_by_cell(
    tasks: list[dict[str, Any]], metrics: dict[str, dict[str, Any]]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        percentile = metrics[task["id"]]["difficulty_percentile"]
        for stratum in ("hard", "medium"):
            if base.is_in_stratum(percentile, stratum):
                by_cell[(task["language"], stratum)].append(task)

    for key, candidates in by_cell.items():
        by_cell[key] = sorted(
            candidates,
            key=lambda task: (-metrics[task["id"]]["median_total_tokens"], task["id"]),
        )
    return dict(by_cell)


def task_record(
    task: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
    stratum: str,
    role: str,
    selection_stage: str,
    within_cell_token_rank: int,
) -> dict[str, Any]:
    return {
        "role": role,
        "selection_stage": selection_stage,
        "within_cell_token_rank": within_cell_token_rank,
        "stratum": stratum,
        "language": task["language"],
        "task_id": task["id"],
        "problem_title": task.get("problem_title"),
        "repository": task.get("repository"),
        **metrics[task["id"]],
    }


def select_20(
    tasks: list[dict[str, Any]], metrics: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    # Calling the original selector both reproduces the original primary sample
    # and assigns the rank-based difficulty percentiles into ``metrics``.
    primary, _ = base.select_tasks(tasks, metrics)
    primary_ids = {row["task_id"] for row in primary}

    tasks_by_id = {task["id"]: task for task in tasks}
    by_cell = ranked_eligible_by_cell(tasks, metrics)

    optional: list[dict[str, Any]] = []
    optional_ids: set[str] = set()
    cell_audit: dict[str, Any] = {}

    # Balanced-first pass: take the second-ranked token task from each cell when
    # one exists. This preserves the original per-language/per-stratum design as
    # far as the benchmark's sparse JS/Rust task counts permit.
    for language, stratum in sorted(by_cell):
        candidates = by_cell[(language, stratum)]
        cell_key = f"{language}:{stratum}"
        cell_audit[cell_key] = {
            "eligible_count": len(candidates),
            "eligible_task_ids_in_token_order": [task["id"] for task in candidates],
            "primary_task_id": candidates[0]["id"],
            "second_distinct_task_available": len(candidates) >= 2,
        }
        if len(candidates) < 2:
            continue
        task = candidates[1]
        optional.append(
            task_record(
                task,
                metrics,
                stratum=stratum,
                role="optional",
                selection_stage="cell_second_choice",
                within_cell_token_rank=2,
            )
        )
        optional_ids.add(task["id"])
        cell_audit[cell_key]["optional_second_task_id"] = task["id"]

    # Fill any shortfall without changing the hard/medium eligibility rules.
    # Ranking is globally by the same secondary token metric, then by stable
    # language/stratum/task identifiers for deterministic ties.
    remaining: list[tuple[dict[str, Any], str, int]] = []
    for (language, stratum), candidates in by_cell.items():
        for rank, task in enumerate(candidates, start=1):
            if task["id"] in primary_ids or task["id"] in optional_ids:
                continue
            remaining.append((task, stratum, rank))

    remaining.sort(
        key=lambda item: (
            -metrics[item[0]["id"]]["median_total_tokens"],
            item[0]["language"],
            item[1],
            item[0]["id"],
        )
    )

    needed = OPTIONAL_TASK_COUNT - len(optional)
    if needed < 0:
        raise RuntimeError("Balanced optional pass produced more than 10 tasks")
    if len(remaining) < needed:
        raise RuntimeError(
            f"Need {needed} fallback tasks but only {len(remaining)} eligible tasks remain"
        )

    fallback_selected: list[str] = []
    for task, stratum, rank in remaining[:needed]:
        optional.append(
            task_record(
                task,
                metrics,
                stratum=stratum,
                role="optional",
                selection_stage="global_token_fill",
                within_cell_token_rank=rank,
            )
        )
        optional_ids.add(task["id"])
        fallback_selected.append(task["id"])

    if len(optional) != OPTIONAL_TASK_COUNT:
        raise RuntimeError(f"Expected 10 optional tasks, got {len(optional)}")
    if primary_ids & optional_ids:
        raise RuntimeError("Primary and optional samples overlap")

    # Keep primary ordering identical to the base script; optional ordering is
    # stable and presentation-oriented rather than selection-significant.
    optional.sort(key=lambda row: (row["stratum"], row["language"], row["task_id"]))

    # Validate all records against the original cells and enrich the primary
    # rows with a role marker while leaving their substantive fields untouched.
    primary_with_role: list[dict[str, Any]] = []
    for row in primary:
        task = tasks_by_id[row["task_id"]]
        candidates = by_cell[(row["language"], row["stratum"])]
        rank = next(i for i, candidate in enumerate(candidates, 1) if candidate["id"] == task["id"])
        primary_with_role.append(
            {
                "role": "primary",
                "selection_stage": "original_cell_first_choice",
                "within_cell_token_rank": rank,
                **row,
            }
        )

    audit = {
        "optional_target_count": OPTIONAL_TASK_COUNT,
        "balanced_second_choices_selected": OPTIONAL_TASK_COUNT - needed,
        "global_fill_slots_needed": needed,
        "global_fill_selected_task_ids": fallback_selected,
        "cells_without_second_distinct_task": sorted(
            key
            for key, value in cell_audit.items()
            if not value["second_distinct_task_available"]
        ),
        "cells": cell_audit,
    }
    return primary_with_role, optional, audit


def print_tasks(title: str, rows: list[dict[str, Any]]) -> None:
    print(title)
    print("=" * len(title))
    for row in rows:
        print(
            f"  {row['stratum']:6s} {row['language']:10s} {row['task_id']:45s} "
            f"difficulty_pct={row['difficulty_percentile']:5.1f} "
            f"median_tokens={row['median_total_tokens'] / 1_000_000:6.2f}M "
            f"stage={row['selection_stage']}"
        )


def print_costs(title: str, estimate: dict[str, Any]) -> None:
    print(f"\n{title}")
    print("=" * len(title))
    for model in estimate["models"]:
        lo = model["projected_total_usd"]
        hi = model["projected_total_upper_usd"]
        span = f"-${hi:.2f}" if hi > lo + 1e-9 else ""
        print(f"  {model['label']:38s} ${lo:.2f}{span}")
    lo = estimate["grand_total_usd"]
    hi = estimate["grand_total_upper_usd"]
    span = f"-${hi:.2f}" if hi > lo + 1e-9 else ""
    print(f"  TOTAL{'':34s} ${lo:.2f}{span}")


def main() -> int:
    args = parse_args()
    if args.harnesses < 1:
        raise SystemExit("--harnesses must be >= 1")

    tasks_url = f"{args.base_url}/{args.version}/tasks.json"
    trials_url = f"{args.base_url}/{args.version}/trials.json"
    release_url = f"{args.base_url}/{args.version}/release.json"
    tasks_doc, tasks_raw, tasks_source = base.load_json_bytes(args.tasks_json, tasks_url)
    trials_doc, trials_raw, trials_source = base.load_json_bytes(args.trials_json, trials_url)
    release_doc, release_raw, release_source = base.load_json_bytes(args.release_json, release_url)

    tasks = tasks_doc["rows"]
    trials = trials_doc["rows"]
    metrics = base.build_task_metrics(tasks, trials)
    primary, optional, audit = select_20(tasks, metrics)
    combined = primary + optional

    if len({row["task_id"] for row in combined}) != 20:
        raise RuntimeError("20-task sample does not contain 20 distinct task IDs")

    store = base.TrajectoryStore(release_doc, args.trajectory_cache_dir)
    primary_cost = base.estimate_costs(primary, trials, args.harnesses, store)
    optional_cost = base.estimate_costs(optional, trials, args.harnesses, store)
    combined_cost = base.estimate_costs(combined, trials, args.harnesses, store)

    result = {
        "method": {
            "benchmark_version": args.version,
            "extension_of": "scripts/select_deepswe_tasks.py",
            "primary_count": 10,
            "optional_count": 10,
            "combined_count": 20,
            "difficulty_and_token_rules": {
                "reference_harness": base.REFERENCE_HARNESS,
                "excluded_evaluated_models": sorted(base.EVALUATED_MODELS),
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
            "optional_extension_rule": (
                "take the second token-ranked eligible task in every language/stratum cell; "
                "if a cell has no second distinct task, fill the remaining optional slots "
                "from all still-unselected hard/medium-eligible tasks by descending median "
                "historical total tokens with stable identifiers as tie-breakers"
            ),
        },
        "sources": {
            "tasks": {"source": tasks_source, "sha256": hashlib.sha256(tasks_raw).hexdigest()},
            "trials": {"source": trials_source, "sha256": hashlib.sha256(trials_raw).hexdigest()},
            "release": {"source": release_source, "sha256": hashlib.sha256(release_raw).hexdigest()},
            "pricing": base.PRICING,
            "cost_trajectory_artifacts": {
                "count": len(store.sha256_by_trial),
                "combined_sha256": hashlib.sha256(
                    "\n".join(
                        f"{trial_name}\t{digest}"
                        for trial_name, digest in sorted(store.sha256_by_trial.items())
                    ).encode("utf-8")
                ).hexdigest(),
                "hash_method": (
                    "SHA-256 of newline-separated, trial-name-sorted '<trial_name>\t<artifact_sha256>' records"
                ),
            },
        },
        "primary_tasks": primary,
        "optional_tasks": optional,
        "combined_tasks": combined,
        "optional_selection_audit": audit,
        "cost_estimates": {
            "primary_10": primary_cost,
            "optional_10": optional_cost,
            "combined_20": combined_cost,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print_tasks("Primary 10 (unchanged)", primary)
    print()
    print_tasks("Optional 10", optional)
    print_costs(f"Optional 10 cost across {args.harnesses} harnesses", optional_cost)
    print_costs(f"Combined 20 cost across {args.harnesses} harnesses", combined_cost)
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
