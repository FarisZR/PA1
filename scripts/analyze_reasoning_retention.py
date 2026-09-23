#!/usr/bin/env python3
"""Check whether earlier reasoning reached the model in each trial (issue #111).

When a model's reasoning is sent back on the next call, it becomes part of the
next prompt, so that prompt grows by at least the reasoning's size. If a gateway
removes it, the prompt grows only by the visible answer and the tool result.

For each call that produced at least MIN_REASONING_TOKENS reasoning tokens, the
script compares the growth of the next prompt with those reasoning tokens. Both
numbers are token counts recorded by the harness.
A trial's retention is the median growth as a percentage of the reasoning:
above 100% when the reasoning arrived, a few percent when it was removed. Calls
with little reasoning are skipped because the tool result would dominate the
growth. Claude Code records no reasoning token counts; for its trials the count
is estimated from the length of the reasoning text (CHARS_PER_TOKEN).

    python3 scripts/analyze_reasoning_retention.py \
        data/benchmark-results/deepseek-v4p1-flash
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

MIN_REASONING_TOKENS = 1000
# Only used for harnesses that record no reasoning token counts (Claude Code).
CHARS_PER_TOKEN = 4
# A trial counts as retaining its reasoning when prompts grew by at least half of it.
RETAINED_THRESHOLD_PERCENT = 50


def reasoning_tokens(step: dict[str, Any]) -> int | None:
    extra = (step.get("metrics") or {}).get("extra") or {}
    return extra.get("reasoning_output_tokens") or extra.get("reasoning_tokens")


def call_pairs(trajectory_path: Path) -> list[dict[str, int]]:
    """Return (reasoning, next-prompt growth) for every call with enough reasoning."""
    trajectory = json.loads(trajectory_path.read_text())
    counted = any(reasoning_tokens(step) for step in trajectory.get("steps", []))
    calls: list[dict[str, int]] = []
    for step in trajectory.get("steps", []):
        if step.get("source") != "agent" or not step.get("metrics"):
            continue
        if (step.get("extra") or {}).get("is_sidechain"):
            continue
        prompt = step["metrics"].get("prompt_tokens") or 0
        if counted:
            reasoning = reasoning_tokens(step) or 0
        else:
            reasoning = len(step.get("reasoning_content") or "") // CHARS_PER_TOKEN
        # Some converters split one model call into several steps with the same usage.
        if calls and calls[-1]["prompt"] == prompt:
            calls[-1]["reasoning"] += reasoning
            continue
        calls.append({"prompt": prompt, "reasoning": reasoning})
    return [
        {"reasoning": before["reasoning"], "growth": after["prompt"] - before["prompt"]}
        for before, after in zip(calls, calls[1:])
        # A shrinking prompt (compaction) has no meaningful growth.
        if before["reasoning"] >= MIN_REASONING_TOKENS and after["prompt"] > before["prompt"]
    ]


def trial_retention(trajectory_path: Path) -> float | None:
    """Median next-prompt growth as a percentage of the preceding reasoning, or None."""
    pairs = call_pairs(trajectory_path)
    if not pairs:
        return None
    return statistics.median(100 * pair["growth"] / pair["reasoning"] for pair in pairs)


def load_rows(job_dir: Path) -> list[dict[str, Any]]:
    """Read the canonical trials of one published job directory."""
    rows = []
    for result_path in sorted(job_dir.glob("*/result.json")):
        result = json.loads(result_path.read_text())
        trajectory_path = result_path.parent / "agent" / "trajectory.json"
        retention = trial_retention(trajectory_path) if trajectory_path.exists() else None
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        exception = result.get("exception_info") or {}
        rows.append(
            {
                "trial": result_path.parent.name,
                "job": Path(result["config"]["trials_dir"]).name,
                "task": result["task_name"].split("/")[-1],
                "harness": result["config"]["agent"]["name"],
                "started_at": result.get("started_at"),
                "reward": rewards.get("reward"),
                "exception": exception.get("exception_type"),
                "retention": retention,
                "retained": None if retention is None else retention >= RETAINED_THRESHOLD_PERCENT,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for job_dir in args.job_dirs:
        print(f"# {job_dir}")
        for row in sorted(load_rows(job_dir), key=lambda r: (r["harness"], r["started_at"] or "")):
            value = "n/a" if row["retention"] is None else f"{row['retention']:.0f}%"
            print(
                f"{row['harness']:12} {row['started_at'][:16]} {row['task'][:40]:40} "
                f"retention={value:>5} reward={row['reward']} {row['exception'] or ''}"
            )


if __name__ == "__main__":
    main()
