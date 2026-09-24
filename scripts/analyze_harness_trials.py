#!/usr/bin/env python3
"""Per-trial outcome, token, context, tool-call, and delegation metrics.

The script reads only the canonical attempts in the normal trial directories of
`data/benchmark-results/` (see its README): each trial's corrected `result.json`
for outcome, token, cost, and context metrics, and its `agent/trajectory.json`
for tool calls and delegation. Non-canonical attempts under `.retry-attempts/`
are never read.

Normalized cost applies the frozen rates in `benchmark/pricing.yaml` to the
recorded uncached-input, cached-input, and output tokens.

Tool calls are counted as the model issued them. A Codex `exec` cell is one call,
categorized by the tools the cell invokes (`apply_patch` makes it an edit).
Claude Code trajectories include the steps of its subagents, so their calls are
counted as part of the trial.

A tool-invocation error is a call that the tool rejected because of the call
itself: invalid arguments or script syntax, an edit whose old text was not found
or not unique, or a missing path or out-of-range offset. Not counted are
non-zero shell exits (often intended, for example a failing test), web fetches
blocked by the sandbox, OpenCode V2 `grep`/`glob` calls, which fail in every
trial because ripgrep cannot be downloaded in the sandbox, and delegation or
skill bookkeeping calls. Excluded calls are also left out of the denominator.

    python3 scripts/analyze_harness_trials.py gpt-5.6-luna \
        data/benchmark-results/luna data/benchmark-results/opencode-v2-luna
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

PRICING_FILE = Path("benchmark/pricing.yaml")
TASK_SELECTION_FILE = Path("data/deepswe_task_selection_v1.1.json")
HARNESS_ORDER = ["codex", "pi", "claude-code", "opencode-v2"]
HARNESS_LABELS = {
    "codex": "Codex",
    "pi": "Pi",
    "claude-code": "Claude Code",
    "opencode-v2": "OpenCode V2",
}

CATEGORY_ORDER = ["read", "edit", "shell", "delegation", "planning", "other"]
CATEGORY_LABELS = {
    "read": "Read and search",
    "edit": "Edit",
    "shell": "Shell",
    "delegation": "Delegation",
    "planning": "Planning",
    "other": "Other",
}
CATEGORY_TOOLS = {
    "read": {"Read", "read", "Grep", "grep", "Glob", "glob"},
    "edit": {"Edit", "edit", "MultiEdit", "Write", "write", "patch", "apply_patch"},
    "shell": {"Bash", "bash", "shell", "exec_command", "write_stdin", "wait"},
    "delegation": {"Agent", "Task", "SendMessage", "TaskStop", "TaskOutput", "ListAgents"},
    "planning": {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "update_plan", "todowrite"},
}
SHELL_TOOLS = {"Bash", "bash", "shell", "exec_command"}
# Calls whose failures are not attributed to the model's tool invocation.
EXCLUDED_FROM_ERRORS = {
    "WebFetch", "webfetch", "Skill", "Agent", "Task", "SendMessage", "TaskStop",
    "TaskOutput", "ListAgents", "ScheduleWakeup", "question",
}
OPENCODE_RIPGREP_TOOLS = {"grep", "glob"}
SPAWN_TOOLS = {"Agent", "Task"}

INVALID_CALL = re.compile(
    r"InputValidationError|Validation failed for tool|SyntaxError|ReferenceError|TypeError"
)
EDIT_MISMATCH = re.compile(
    r"String to replace not found|Found \d+ matches|No changes to make|Could not find"
    r"|Found \d+ occurrences|apply_patch verification failed|oldText must"
)
MISSING_PATH = re.compile(r"File does not exist|ENOENT|No such file")
OUT_OF_RANGE = re.compile(r"exceeds maximum allowed|out of range|beyond end of file")


def model_rates(model: str, pricing_file: Path = PRICING_FILE) -> dict[str, float]:
    """Read one model's USD-per-million rates from the pricing table."""
    rates: dict[str, float] = {}
    in_model = False
    for line in pricing_file.read_text().splitlines():
        if line.startswith(f"  {model}:"):
            in_model = True
            continue
        if in_model and line.startswith("  ") and not line.startswith("    "):
            break
        match = re.match(r"\s{4}(input|cached_input|output):\s*([0-9.]+)", line)
        if in_model and match:
            rates[match.group(1)] = float(match.group(2))
    if set(rates) != {"input", "cached_input", "output"}:
        raise ValueError(f"Incomplete pricing for {model}: {rates}")
    return rates


def task_catalog(selection_file: Path = TASK_SELECTION_FILE) -> list[dict[str, str]]:
    """Return the selected tasks, hard tasks first, with a short display label."""
    languages = {"javascript": "JavaScript", "typescript": "TypeScript"}
    tasks = json.loads(selection_file.read_text())["selected_tasks"]
    catalog = [
        {
            "task": task["task_id"],
            "label": f"{task['repository'].split('/')[-1]} "
            f"({languages.get(task['language'], task['language'].capitalize())})",
            "stratum": task["stratum"],
            "language": task["language"],
        }
        for task in tasks
    ]
    return sorted(catalog, key=lambda task: (task["stratum"] != "hard", task["language"]))


def _seconds(span: dict[str, Any] | None) -> float | None:
    if not span or not span.get("finished_at"):
        return None
    start = datetime.fromisoformat(span["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(span["finished_at"].replace("Z", "+00:00"))
    return (end - start).total_seconds()


def _codex_tools(call: dict[str, Any]) -> list[str]:
    source = str((call.get("arguments") or {}).get("input") or "")
    return re.findall(r"tools\.([a-zA-Z_]+)\(", source)


def _category(harness: str, call: dict[str, Any]) -> str:
    name = call.get("function_name")
    if harness == "codex" and name == "exec":
        tools = set(_codex_tools(call))
        if "apply_patch" in tools:
            return "edit"
        if "update_plan" in tools and not tools & CATEGORY_TOOLS["shell"]:
            return "planning"
        return "shell"
    for category, names in CATEGORY_TOOLS.items():
        if name in names:
            return category
    return "other"


def _error_class(text: str) -> str:
    if INVALID_CALL.search(text):
        return "invalid_call"
    if EDIT_MISMATCH.search(text):
        return "edit_mismatch"
    if MISSING_PATH.search(text):
        return "missing_path"
    if OUT_OF_RANGE.search(text):
        return "out_of_range"
    return "other_error"


def _invocation_error(harness: str, call: dict[str, Any], observation: dict[str, Any]) -> str | None:
    """Return the error class of a failed tool invocation, or None."""
    text = str(observation.get("content") or "")
    if harness == "codex":
        if "Script failed" not in text[:200]:
            return None
        detail = text.split("Script error:", 1)[-1]
        if "exec_command failed" in detail:
            return None  # The process could not be spawned; not a malformed call.
        return _error_class(detail)
    flagged = bool((observation.get("extra") or {}).get("is_error"))
    flagged = flagged or "[error] tool reported failure" in text
    if not flagged or call.get("function_name") in SHELL_TOOLS:
        return None
    if harness == "opencode-v2":
        # Pier's OpenCode V2 converter keeps the error flag but not its text.
        # Every remaining OpenCode error was checked in the raw stream logs.
        return "edit_mismatch" if _category(harness, call) == "edit" else "read_error"
    return _error_class(text)


def trajectory_metrics(harness: str, path: Path) -> dict[str, Any]:
    trajectory = json.loads(path.read_text())
    categories = dict.fromkeys(CATEGORY_ORDER, 0)
    errors: dict[str, int] = {}
    errors_by_category = dict.fromkeys(CATEGORY_ORDER, 0)
    counted_calls = 0
    edit_calls = 0
    edit_errors = 0
    spawns = 0
    background_spawns = 0
    spawn_models: dict[str, int] = {}
    spawn_limit_rejections = 0
    web_fetches = 0
    tokens = {"main_input": 0, "main_output": 0, "sub_input": 0, "sub_output": 0}
    sub_cached = 0
    sub_peak = 0

    for step in trajectory.get("steps", []):
        if step.get("source") != "agent":
            continue
        sidechain = bool((step.get("extra") or {}).get("is_sidechain"))
        metrics = step.get("metrics") or {}
        prefix = "sub" if sidechain else "main"
        tokens[f"{prefix}_input"] += metrics.get("prompt_tokens") or 0
        tokens[f"{prefix}_output"] += metrics.get("completion_tokens") or 0
        if sidechain:
            sub_cached += metrics.get("cached_tokens") or 0
            sub_peak = max(sub_peak, metrics.get("prompt_tokens") or 0)

        results = (step.get("observation") or {}).get("results") or []
        by_call = {result.get("source_call_id"): result for result in results}
        for call in step.get("tool_calls") or []:
            name = call.get("function_name")
            category = _category(harness, call)
            categories[category] += 1
            if name in SPAWN_TOOLS:
                arguments = call.get("arguments") or {}
                observation = by_call.get(call.get("tool_call_id")) or {}
                if "subagent limit reached" in str(observation.get("content")):
                    spawn_limit_rejections += 1
                else:
                    spawns += 1
                    background_spawns += bool(arguments.get("run_in_background"))
                    requested = str(arguments.get("model") or "default")
                    spawn_models[requested] = spawn_models.get(requested, 0) + 1
            web_fetches += name in {"WebFetch", "webfetch"}
            if name in EXCLUDED_FROM_ERRORS:
                continue
            if harness == "opencode-v2" and name in OPENCODE_RIPGREP_TOOLS:
                continue
            counted_calls += 1
            edit_calls += category == "edit"
            error = _invocation_error(harness, call, by_call.get(call.get("tool_call_id")) or {})
            if error:
                errors[error] = errors.get(error, 0) + 1
                errors_by_category[category] += 1
                edit_errors += category == "edit"

    return {
        "tool_calls": sum(categories.values()),
        "calls_by_category": categories,
        "error_denominator": counted_calls,
        "invocation_errors": sum(errors.values()),
        "errors_by_class": errors,
        "errors_by_category": errors_by_category,
        "edit_calls": edit_calls,
        "edit_errors": edit_errors,
        "subagent_spawns": spawns,
        "background_spawns": background_spawns,
        "spawn_models": spawn_models,
        "spawn_limit_rejections": spawn_limit_rejections,
        "web_fetches": web_fetches,
        "subagent_input_tokens": tokens["sub_input"],
        "subagent_output_tokens": tokens["sub_output"],
        "subagent_cached_tokens": sub_cached,
        "subagent_peak_context_tokens": sub_peak,
        "main_input_tokens": tokens["main_input"],
        "main_output_tokens": tokens["main_output"],
    }


def load_trials(model: str, job_dirs: list[Path]) -> list[dict[str, Any]]:
    """Return one row per canonical trial in the given job directories."""
    rates = model_rates(model)
    rows = []
    for job_dir in job_dirs:
        for result_path in sorted(job_dir.glob("*/result.json")):
            result = json.loads(result_path.read_text())
            harness = result["config"]["agent"]["name"]
            agent = result.get("agent_result") or {}
            rewards = (result.get("verifier_result") or {}).get("rewards") or {}
            exception = (result.get("exception_info") or {}).get("exception_type")
            input_tokens = agent.get("n_input_tokens") or 0
            cached = agent.get("n_cache_tokens") or 0
            output = agent.get("n_output_tokens") or 0
            row = {
                "job": job_dir.name,
                "trial": result_path.parent.name,
                "task": result["task_name"].split("/")[-1],
                "harness": harness,
                "passed": rewards.get("reward") == 1,
                "f2p_passed": rewards.get("f2p_passed"),
                "f2p_total": rewards.get("f2p_total"),
                "p2p_passed": rewards.get("p2p_passed"),
                "p2p_total": rewards.get("p2p_total"),
                "exception": exception,
                "timeout": exception == "AgentTimeoutError",
                "input_tokens": input_tokens,
                "cached_tokens": cached,
                "output_tokens": output,
                "cost_usd": (
                    (input_tokens - cached) * rates["input"]
                    + cached * rates["cached_input"]
                    + output * rates["output"]
                ) / 1_000_000,
                "peak_context_tokens": agent.get("peak_context_tokens"),
                "compactions": agent.get("summarization_count") or 0,
                "agent_steps": agent.get("n_agent_steps"),
                "agent_minutes": (_seconds(result.get("agent_execution")) or 0) / 60,
            }
            row.update(trajectory_metrics(harness, result_path.parent / "agent" / "trajectory.json"))
            row["root_cached_tokens"] = cached - row["subagent_cached_tokens"]
            rows.append(row)
    return rows


def _present(group: list[dict[str, Any]], key: str) -> list[Any]:
    """Values of one metric, without trials whose agent result did not record it."""
    return [row[key] for row in group if row[key] is not None]


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate the per-trial rows by harness."""
    summary = {}
    for harness in HARNESS_ORDER:
        group = [row for row in rows if row["harness"] == harness]
        if not group:
            continue
        passing = [row for row in group if row["passed"]]
        total_input = sum(row["input_tokens"] for row in group)
        peaks = _present(group, "peak_context_tokens")
        steps = _present(group, "agent_steps")
        summary[harness] = {
            "trials": len(group),
            "passed": len(passing),
            "timeouts": sum(row["timeout"] for row in group),
            "cost_usd": sum(row["cost_usd"] for row in group),
            "median_cost_usd": statistics.median(row["cost_usd"] for row in group),
            "median_input_tokens": statistics.median(row["input_tokens"] for row in group),
            "median_output_tokens": statistics.median(row["output_tokens"] for row in group),
            "cache_hit_share": sum(row["cached_tokens"] for row in group) / total_input,
            "median_peak_context_tokens": statistics.median(peaks) if peaks else None,
            "max_peak_context_tokens": max(peaks) if peaks else None,
            "compactions": sum(row["compactions"] for row in group),
            "median_agent_minutes": statistics.median(row["agent_minutes"] for row in group),
            "median_agent_steps": statistics.median(steps) if steps else None,
            "tool_calls": sum(row["tool_calls"] for row in group),
            "invocation_error_share": sum(row["invocation_errors"] for row in group)
            / sum(row["error_denominator"] for row in group),
            "edit_error_share": sum(row["edit_errors"] for row in group)
            / max(1, sum(row["edit_calls"] for row in group)),
            "subagent_spawns": sum(row["subagent_spawns"] for row in group),
            "web_fetches": sum(row["web_fetches"] for row in group),
        }
    return summary


def paired_ratio(rows: list[dict[str, Any]], metric: str, harness: str, reference: str) -> float:
    """Geometric mean over tasks of one harness's metric relative to a reference harness."""
    by_task: dict[str, dict[str, float]] = {}
    for row in rows:
        by_task.setdefault(row["task"], {})[row["harness"]] = row[metric]
    logs = [
        math.log(values[harness] / values[reference])
        for values in by_task.values()
        if values.get(harness) and values.get(reference)
    ]
    return math.exp(sum(logs) / len(logs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", help="Model key in benchmark/pricing.yaml, e.g. gpt-5.6-luna")
    parser.add_argument("job_dirs", nargs="+", type=Path, help="Job directories with canonical trials")
    parser.add_argument("--trials", action="store_true", help="Print per-trial rows instead of the summary")
    args = parser.parse_args()
    rows = load_trials(args.model, args.job_dirs)
    print(json.dumps(rows if args.trials else summarize(rows), indent=2, default=str))


if __name__ == "__main__":
    main()
