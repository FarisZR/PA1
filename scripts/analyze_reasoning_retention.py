#!/usr/bin/env python3
"""Check whether earlier reasoning reached the model in each trial (issue #111).

A thinking model's reasoning from one call is sent back on the next call. If it
reaches the model, the provider counts it as prompt input, so the next prompt
grows by at least the size of that reasoning. If a gateway removes it, the
prompt grows only by the visible output and the tool result.

For every pair of consecutive main-thread calls whose first call produced at
least MIN_REASONING_CHARS of reasoning text, the script divides the prompt
increase by the reasoning size in tokens (characters / CHARS_PER_TOKEN). The
trial's retention is the median of these ratios: about 1 or more when the
reasoning was retained, near 0 when it was removed. Trials without such a pair
have no retention value.

Only reasoning that is recorded as plain text can be checked this way. GPT-5.6
Luna's reasoning is encrypted, so its trajectories are not covered.

    python3 scripts/analyze_reasoning_retention.py \
        data/benchmark-results/deepseek-v4p1-flash
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

MIN_REASONING_CHARS = 4000
CHARS_PER_TOKEN = 4.0
RETAINED_THRESHOLD = 0.5


def trial_retention(trajectory_path: Path) -> float | None:
    """Return the median prompt growth per earlier reasoning token, or None."""
    trajectory = json.loads(trajectory_path.read_text())
    calls: list[dict[str, int]] = []
    for step in trajectory.get("steps", []):
        if step.get("source") != "agent" or not step.get("metrics"):
            continue
        if (step.get("extra") or {}).get("is_sidechain"):
            continue
        prompt = step["metrics"].get("prompt_tokens") or 0
        reasoning = len(step.get("reasoning_content") or "")
        # Some converters split one model call into several steps with the same usage.
        if calls and calls[-1]["prompt"] == prompt:
            calls[-1]["reasoning"] += reasoning
            continue
        calls.append({"prompt": prompt, "reasoning": reasoning})

    ratios = [
        (after["prompt"] - before["prompt"]) / (before["reasoning"] / CHARS_PER_TOKEN)
        for before, after in zip(calls, calls[1:])
        if before["reasoning"] >= MIN_REASONING_CHARS and after["prompt"] > before["prompt"]
    ]
    return statistics.median(ratios) if ratios else None


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
                "task": result["task_name"].split("/")[-1],
                "harness": result["config"]["agent"]["name"],
                "started_at": result.get("started_at"),
                "reward": rewards.get("reward"),
                "exception": exception.get("exception_type"),
                "retention": retention,
                "retained": None if retention is None else retention >= RETAINED_THRESHOLD,
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
            value = "n/a" if row["retention"] is None else f"{row['retention']:.2f}"
            print(
                f"{row['harness']:12} {row['started_at'][:16]} {row['task'][:40]:40} "
                f"retention={value:>5} reward={row['reward']} {row['exception'] or ''}"
            )


if __name__ == "__main__":
    main()
