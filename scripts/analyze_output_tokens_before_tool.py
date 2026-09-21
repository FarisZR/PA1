#!/usr/bin/env python3
"""Analyze model output tokens in benchmark responses that invoke tools.

The benchmark's normalized ATIF trajectories record one model response per
``steps`` entry.  For an agent step with ``tool_calls``,
``metrics.completion_tokens`` is the provider-reported number of completion
(output) tokens generated before the tool call(s).  This script reports the
largest such response and summarizes the distribution by agent/model.

Only recorded usage is used.  The script does not estimate tokens from the
serialized message text, which would be inaccurate for reasoning tokens and
provider-specific tokenizers.

Examples:
    python3 scripts/analyze_output_tokens_before_tool.py
    python3 scripts/analyze_output_tokens_before_tool.py --top 50
    python3 scripts/analyze_output_tokens_before_tool.py --format json \
        --output /tmp/output-token-report.json
    python3 scripts/analyze_output_tokens_before_tool.py --exclude-retries
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_RUNS_ROOT = Path("benchmark/runs")
TRAJECTORY_NAME = "trajectory.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "runs_root",
        nargs="?",
        type=Path,
        default=DEFAULT_RUNS_ROOT,
        help="Benchmark runs directory or a trajectory.json file (default: benchmark/runs).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of highest-token responses to print (default: 20).",
    )
    parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        help="Restrict the report to an agent name; may be repeated.",
    )
    parser.add_argument(
        "--exclude-retries",
        action="store_true",
        help="Exclude trajectories below a .retry-attempts directory.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Also write the complete JSON report to this path.",
    )
    args = parser.parse_args()
    if args.top < 1:
        parser.error("--top must be at least 1")
    return args


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trajectory_paths(root: Path, exclude_retries: bool) -> list[Path]:
    if root.is_file():
        paths = [root] if root.name == TRAJECTORY_NAME else []
    else:
        paths = sorted(root.rglob(TRAJECTORY_NAME)) if root.exists() else []

    if exclude_retries:
        paths = [path for path in paths if ".retry-attempts" not in path.parts]
    return paths


def run_metadata(path: Path, runs_root: Path) -> dict[str, Any]:
    """Return stable, human-readable provenance for a trajectory path."""
    agent_dir = path.parent
    attempt_dir = agent_dir.parent
    if attempt_dir.name.startswith("attempt-"):
        trial_dir = attempt_dir.parent
        attempt = attempt_dir.name
    else:
        trial_dir = attempt_dir
        attempt = None

    try:
        relative_path = str(path.relative_to(runs_root))
    except ValueError:
        relative_path = str(path)

    trial_name = trial_dir.name
    task_name = trial_name.split("__", 1)[0]
    job_name = trial_dir.parent.name
    if job_name == ".retry-attempts":
        job_name = trial_dir.parent.parent.name

    return {
        "path": relative_path,
        "job": job_name,
        "trial": trial_name,
        "task": task_name,
        "attempt": attempt,
    }


def nested_number(mapping: Any, *keys: str) -> int | float | None:
    if not isinstance(mapping, dict):
        return None
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if is_number(value) else None


def reasoning_tokens(metrics: dict[str, Any]) -> int | float | None:
    """Extract provider-specific reasoning-token fields when they are present."""
    extra = metrics.get("extra")
    for key in ("reasoning_output_tokens", "reasoning_tokens"):
        value = nested_number(extra, key)
        if value is not None:
            return value

    # Claude Code trajectories put provider details under
    # metrics.extra.output_tokens_details.thinking_tokens.
    return nested_number(extra, "output_tokens_details", "thinking_tokens")


def tool_names(tool_calls: list[Any]) -> list[str]:
    names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = call.get("function_name") or call.get("name")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def record_for_step(
    *,
    path: Path,
    runs_root: Path,
    trajectory: dict[str, Any],
    step: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    tool_calls = step.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return None, None

    metrics = step.get("metrics")
    if not isinstance(metrics, dict) or not is_number(metrics.get("completion_tokens")):
        return None, "missing metrics.completion_tokens"

    agent = trajectory.get("agent")
    agent = agent if isinstance(agent, dict) else {}
    step_extra = step.get("extra")
    step_extra = step_extra if isinstance(step_extra, dict) else {}
    metrics_extra = metrics.get("extra")
    metrics_extra = metrics_extra if isinstance(metrics_extra, dict) else {}

    metadata = run_metadata(path, runs_root)
    completion = metrics["completion_tokens"]
    # JSON usage is normally integral. Preserve a non-integral value if a
    # provider ever emits one rather than silently rounding it.
    if isinstance(completion, float) and completion.is_integer():
        completion = int(completion)

    record: dict[str, Any] = {
        **metadata,
        "agent": agent.get("name"),
        "agent_version": agent.get("version"),
        "model": agent.get("model_name"),
        "step_id": step.get("step_id"),
        "completion_tokens": completion,
        "prompt_tokens": metrics.get("prompt_tokens"),
        "cached_tokens": metrics.get("cached_tokens"),
        "reasoning_tokens": reasoning_tokens(metrics),
        "tool_call_count": len(tool_calls),
        "tool_names": tool_names(tool_calls),
        "stop_reason": step_extra.get("stop_reason") or metrics_extra.get("stop_reason"),
    }
    return record, None


def analyze(
    runs_root: Path,
    *,
    agents: set[str] | None = None,
    exclude_retries: bool = False,
    top: int = 20,
) -> dict[str, Any]:
    paths = trajectory_paths(runs_root, exclude_retries)
    records: list[dict[str, Any]] = []
    missing_usage: list[dict[str, Any]] = []
    invalid_files: list[dict[str, str]] = []
    total_agent_steps = 0
    total_tool_steps = 0

    for path in paths:
        try:
            trajectory = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            invalid_files.append({"path": str(path), "error": str(error)})
            continue
        if not isinstance(trajectory, dict):
            invalid_files.append({"path": str(path), "error": "top-level JSON is not an object"})
            continue

        agent = trajectory.get("agent")
        agent_name = agent.get("name") if isinstance(agent, dict) else None
        if agents and agent_name not in agents:
            continue

        steps = trajectory.get("steps")
        if not isinstance(steps, list):
            invalid_files.append({"path": str(path), "error": "steps is not an array"})
            continue

        for step in steps:
            if not isinstance(step, dict) or step.get("source") != "agent":
                continue
            total_agent_steps += 1
            tool_calls = step.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                continue
            total_tool_steps += 1
            record, missing_reason = record_for_step(
                path=path, runs_root=runs_root, trajectory=trajectory, step=step
            )
            if record is not None:
                records.append(record)
            elif missing_reason is not None:
                metadata = run_metadata(path, runs_root)
                missing_usage.append(
                    {
                        **metadata,
                        "agent": agent_name,
                        "model": agent.get("model_name") if isinstance(agent, dict) else None,
                        "step_id": step.get("step_id"),
                        "tool_call_count": len(tool_calls),
                        "reason": missing_reason,
                    }
                )

    records.sort(key=lambda record: (-record["completion_tokens"], record["path"], record["step_id"] or 0))

    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["agent"], record["model"])].append(record)

    by_agent_model = []
    for (agent, model), group in sorted(grouped.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        values = [record["completion_tokens"] for record in group]
        maximum = max(group, key=lambda record: record["completion_tokens"])
        by_agent_model.append(
            {
                "agent": agent,
                "model": model,
                "responses_with_tool_calls": len(group),
                "max_completion_tokens": maximum["completion_tokens"],
                "median_completion_tokens": statistics.median(values),
                "mean_completion_tokens": round(statistics.mean(values), 2),
                "max_record": maximum,
            }
        )

    report = {
        "schema_version": 1,
        "scope": {
            "runs_root": str(runs_root),
            "trajectory_files": len(paths),
            "exclude_retries": exclude_retries,
            "agents": sorted(agents) if agents else None,
        },
        "metric": {
            "name": "completion_tokens_before_tool_call",
            "definition": (
                "steps[*].metrics.completion_tokens for agent steps with a non-empty "
                "steps[*].tool_calls array"
            ),
            "token_source": "provider-reported trajectory usage",
            "text_token_estimation": False,
        },
        "summary": {
            "trajectory_files": len(paths),
            "invalid_files": len(invalid_files),
            "agent_steps": total_agent_steps,
            "agent_steps_with_tool_calls": total_tool_steps,
            "responses_with_recorded_usage": len(records),
            "responses_missing_completion_usage": len(missing_usage),
        },
        "global_max": records[0] if records else None,
        "top": records[:top],
        "by_agent_model": by_agent_model,
        "missing_usage": missing_usage,
        "invalid_files": invalid_files,
    }
    return report


def format_tokens(value: Any) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def format_record(record: dict[str, Any], rank: int | None = None) -> str:
    prefix = f"{rank:>2}. " if rank is not None else ""
    location = f"{record['job']}/{record['trial']} step {record['step_id']}"
    tool_names = ", ".join(record["tool_names"]) or "unknown tool"
    return (
        f"{prefix}{format_tokens(record['completion_tokens']):>8} tokens  "
        f"{record['agent']} / {record['model']}  {location}  "
        f"({record['tool_call_count']} tool call(s): {tool_names})"
    )


def text_report(report: dict[str, Any], top: int) -> str:
    summary = report["summary"]
    scope = report["scope"]
    lines = [
        "Benchmark output tokens before tool calls",
        "===========================================",
        f"Trajectory files: {summary['trajectory_files']} (root: {scope['runs_root']})",
        f"Agent steps with tool calls: {summary['agent_steps_with_tool_calls']}",
        f"Recorded usage: {summary['responses_with_recorded_usage']}",
        f"Missing completion usage: {summary['responses_missing_completion_usage']}",
        "",
        "Metric: provider-reported steps[*].metrics.completion_tokens on an agent",
        "step with at least one steps[*].tool_calls entry. This includes reasoning",
        "tokens when the provider includes them in completion_tokens; no text estimate",
        "is performed.",
        "",
    ]

    maximum = report["global_max"]
    if maximum is None:
        lines.append("No tool-calling agent steps with recorded completion usage were found.")
    else:
        lines.extend(
            [
                f"Highest recorded completion output: {format_tokens(maximum['completion_tokens'])} tokens",
                format_record(maximum),
                "",
                f"Top {min(top, len(report['top']))} responses:",
            ]
        )
        lines.extend(format_record(record, index) for index, record in enumerate(report["top"], 1))

    lines.extend(["", "Maximum by agent/model:"])
    for group in report["by_agent_model"]:
        lines.append(
            f"{group['agent']} / {group['model']}: "
            f"max {format_tokens(group['max_completion_tokens'])}, "
            f"median {format_tokens(group['median_completion_tokens'])}, "
            f"{group['responses_with_tool_calls']} tool-calling responses"
        )

    if report["missing_usage"]:
        lines.extend(
            [
                "",
                "Steps excluded because completion usage was unavailable:",
            ]
        )
        lines.extend(
            f"- {item['job']}/{item['trial']} step {item['step_id']} "
            f"({item['agent']} / {item['model']}; {item['reason']})"
            for item in report["missing_usage"]
        )

    if report["invalid_files"]:
        lines.extend(["", "Invalid trajectory files:"])
        lines.extend(f"- {item['path']}: {item['error']}" for item in report["invalid_files"])

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    runs_root = args.runs_root
    report = analyze(
        runs_root,
        agents=set(args.agents) if args.agents else None,
        exclude_retries=args.exclude_retries,
        top=args.top,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(text_report(report, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
