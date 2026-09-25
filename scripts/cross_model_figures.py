"""Cross-model data and figures for the discussion chapter.

Every function reads the canonical trials of all four models (or data derived
from them) and draws one figure of the chapter. Harness colors, markers, and
axis styling come from ``scripts.harness_figures`` so that the figures match
the model sections.
"""

from __future__ import annotations

import collections
import csv
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

from scripts.analyze_harness_trials import (
    EXCLUDED_FROM_ERRORS,
    HARNESS_LABELS,
    HARNESS_ORDER,
    OPENCODE_RIPGREP_TOOLS,
    _invocation_error,
    load_trials,
)
from scripts.harness_figures import GRID, HARNESS_COLORS, HARNESS_MARKERS, INK, MUTED, _style_axis

RESULTS = Path("data/benchmark-results")
# Pricing key, display label, and job directories with canonical trials.
MODELS = {
    "deepseek-v4p1-flash": ("DeepSeek V4.1 Flash",
                            [RESULTS / "deepseek-v4p1-flash", RESULTS / "opencode-v2-deepseek-v4p1-flash"]),
    "gpt-5.6-luna": ("GPT-5.6 Luna", [RESULTS / "luna", RESULTS / "opencode-v2-luna"]),
    "glm-5p3-flash": ("GLM-5.3-Flash", [RESULTS / "glm-5.3-flash", RESULTS / "opencode-v2-glm-5.3-flash"]),
    "kimi-k3": ("Kimi K3", [RESULTS / "kimi-k3"]),
}
DEEPSWE_REFERENCE = Path("data/deepswe-reference/mini-swe-agent-rollouts.csv")
TASK_SELECTION = Path("data/deepswe_task_selection_v1.1.json")
SHELL_TOOLS = {"bash", "Bash", "shell"}
NEUTRAL = "#8a8983"
SHORT = {"deepseek-v4p1-flash": "DeepSeek", "gpt-5.6-luna": "Luna", "glm-5p3-flash": "GLM", "kimi-k3": "Kimi"}
BAND = "#e3efdd"
KIMI_BAND = "#f4f3ef"


def _root_steps(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in trajectory.get("steps", [])
            if step.get("source") == "agent" and not (step.get("extra") or {}).get("is_sidechain")]


def _extra_metrics(harness: str, path: Path) -> dict[str, Any]:
    """Root-session metrics that the model sections do not need."""
    steps = _root_steps(json.loads(path.read_text()))
    first_prompt = next(((s.get("metrics") or {}).get("prompt_tokens") for s in steps
                         if (s.get("metrics") or {}).get("prompt_tokens")), None)
    steps_with_calls = multi_steps = codex_cells = codex_multi = shell = shell_timeout = 0
    root_counted = root_errors = 0
    for step in steps:
        calls = step.get("tool_calls") or []
        steps_with_calls += bool(calls)
        multi_steps += len(calls) > 1
        observations = {r.get("source_call_id"): r for r in (step.get("observation") or {}).get("results") or []}
        for call in calls:
            name, arguments = call.get("function_name"), call.get("arguments") or {}
            if harness == "codex" and name == "exec":
                codex_cells += 1
                codex_multi += len(re.findall(r"tools\.\w+\(", arguments.get("input", ""))) > 1
            if name in SHELL_TOOLS:
                shell += 1
                shell_timeout += "timeout" in arguments
            if name in EXCLUDED_FROM_ERRORS or (harness == "opencode-v2" and name in OPENCODE_RIPGREP_TOOLS):
                continue
            root_counted += 1
            root_errors += bool(_invocation_error(harness, call, observations.get(call.get("tool_call_id")) or {}))
    return {"root_steps": len(steps), "first_prompt_tokens": first_prompt, "steps_with_calls": steps_with_calls, "multi_call_steps": multi_steps,
            "codex_cells": codex_cells, "codex_multi_op_cells": codex_multi, "shell_calls": shell,
            "shell_calls_with_timeout": shell_timeout, "root_counted_calls": root_counted,
            "root_invocation_errors": root_errors}


def load_cross_model() -> pd.DataFrame:
    """One row per canonical trial of every model, with the extra root-session metrics."""
    rows = []
    for model, (label, jobs) in MODELS.items():
        for row in load_trials(model, jobs):
            trajectory = next(job / row["trial"] / "agent" / "trajectory.json" for job in jobs
                              if (job / row["trial"]).exists())
            rows.append({**row, **_extra_metrics(row["harness"], trajectory), "model": model, "model_label": label})
    return pd.DataFrame(rows)


def model_order(frame: pd.DataFrame) -> list[str]:
    """Models from the cheapest to the most expensive (median of the harnesses' mean cost per task)."""
    cost = frame.groupby(["model", "harness"])["cost_usd"].mean().groupby("model").median()
    return list(cost.sort_values().index)


def complete_models(frame: pd.DataFrame) -> list[str]:
    """Models that ran in all four harnesses."""
    counts = frame.groupby("model")["harness"].nunique()
    return [m for m in model_order(frame) if counts[m] == len(HARNESS_ORDER)]


def _cells(frame: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    return {key: group for key, group in frame.groupby(["model", "harness"])}


def _model_axis(ax, frame: pd.DataFrame, models: list[str], with_cost: bool = False) -> None:
    labels = []
    for model in models:
        label = MODELS[model][0]
        if with_cost:
            cost = frame[frame["model"] == model].groupby("harness")["cost_usd"].mean().median()
            label += f"\n~USD {cost:.2f} per task"
        labels.append(label)
    ax.set_xticks(range(len(models)), labels, fontsize=7.5)
    ax.set_xlim(-0.5, len(models) - 0.5)
    for i, model in enumerate(models):
        if frame[frame["model"] == model]["harness"].nunique() < len(HARNESS_ORDER):
            ax.axvspan(i - 0.5, i + 0.5, color=KIMI_BAND, zorder=0)


def _harness_legend(ax, extra: list | None = None, **kwargs) -> None:
    handles = [Line2D([], [], color=HARNESS_COLORS[h], marker=HARNESS_MARKERS[h], linestyle="-", markersize=5,
                      label=HARNESS_LABELS[h]) for h in HARNESS_ORDER] + (extra or [])
    ax.legend(handles=handles, frameon=False, fontsize=7, **kwargs)


# --- Task success --------------------------------------------------------------------------

def mini_swe_reference() -> dict[str, dict[str, float]]:
    """Average and middle 68% of the single-run scores possible from DeepSWE's rollouts.

    A single run takes one rollout per task, so its score is the sum of independent
    per-task Bernoulli outcomes with the observed solve rates.
    """
    rates: dict[str, dict[str, list[bool]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    with DEEPSWE_REFERENCE.open() as handle:
        for row in csv.DictReader(handle):
            if "not V4.1" not in row["pa1_model"]:
                rates[row["pa1_model"]][row["task_name"]].append(row["passed"] == "True")
    reference = {}
    for model, tasks in rates.items():
        distribution = [1.0]
        for outcomes in tasks.values():
            p = sum(outcomes) / len(outcomes)
            distribution = [(distribution[k] if k < len(distribution) else 0) * (1 - p)
                            + (distribution[k - 1] * p if k else 0) for k in range(len(distribution) + 1)]
        cumulative = [sum(distribution[:k + 1]) for k in range(len(distribution))]
        quantile = lambda q: next(k for k, c in enumerate(cumulative) if c >= q)
        reference[model] = {"mean": sum(k * v for k, v in enumerate(distribution)),
                            "low": quantile(0.16), "high": quantile(0.84),
                            "runs": statistics.mean(len(o) for o in tasks.values()),
                            "sd": math.sqrt(sum((sum(o) / len(o)) * (1 - sum(o) / len(o)) * len(o) / (len(o) - 1)
                                                for o in tasks.values()))}
    return reference


def plot_success_by_model(frame: pd.DataFrame):
    """Tasks passed per harness and model, with the mini-swe-agent reference and the top band."""
    models = model_order(frame)
    cells = _cells(frame)
    reference = mini_swe_reference()
    fig, ax = plt.subplots(figsize=(6.3, 3.9))
    best = {m: max(g["passed"].sum() for (mm, _), g in cells.items() if mm == m) for m in models}
    # Every model's best harness passed 7 or 8 tasks, so one band marks "best or one task behind".
    ax.axhspan(max(best.values()) - 1.35, max(best.values()) + 0.35, color=BAND, zorder=0)
    _model_axis(ax, frame, models, with_cost=True)
    offsets = {h: (i - 1.5) * 0.07 for i, h in enumerate(HARNESS_ORDER)}
    for harness in HARNESS_ORDER:
        present = [m for m in models if (m, harness) in cells]
        xs = [models.index(m) + offsets[harness] for m in present]
        ys = [cells[(m, harness)]["passed"].sum() for m in present]
        ax.plot(xs, ys, color=HARNESS_COLORS[harness], marker=HARNESS_MARKERS[harness], markersize=6.5,
                linewidth=1.8, markeredgecolor="white", markeredgewidth=0.8, zorder=3)
    for model in models:
        if model in reference:
            r = reference[model]
            ax.errorbar(models.index(model) + 0.33, r["mean"], yerr=[[r["mean"] - r["low"]], [r["high"] - r["mean"]]],
                        fmt="o", color=NEUTRAL, markerfacecolor="white", markeredgewidth=1.4, markersize=6,
                        capsize=3, linewidth=1.1, zorder=4)
        else:
            ax.text(models.index(model) + 0.3, 1.2, "no DeepSWE\nreference", fontsize=6.5, color=MUTED, ha="center")
    ax.set_ylim(0, 10)
    ax.set_yticks(range(0, 11, 2))
    ax.set_ylabel("Tasks passed (of 10)", fontsize=8)
    _style_axis(ax)
    _harness_legend(ax, [Line2D([], [], color=NEUTRAL, marker="o", markerfacecolor="white", linestyle="",
                                label="mini-swe-agent (DeepSWE)"),
                         Patch(color=BAND, label="best or 1 task behind")],
                    ncol=3, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return fig


def exclusive_outcomes(frame: pd.DataFrame) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Tasks only one harness solved or only one failed, on the models with all four harnesses."""
    solve_rate = {t["task_id"]: t for t in json.loads(TASK_SELECTION.read_text())["selected_tasks"]}
    result = {h: {"solved": [], "failed": []} for h in HARNESS_ORDER}
    core = frame[frame["model"].isin(complete_models(frame))]
    for (model, task), group in core.groupby(["model", "task"]):
        passed, failed = group[group["passed"]], group[~group["passed"]]
        info = solve_rate[task]
        entry = {"model": SHORT[model], "task": info["repository"].split("/")[-1].replace("python-", ""),
                 "solve_rate": info["solve_rate"]}
        if len(passed) == 1:
            result[passed.iloc[0]["harness"]]["solved"].append({**entry, "weight": 1 - info["solve_rate"]})
        if len(failed) == 1:
            result[failed.iloc[0]["harness"]]["failed"].append({**entry, "weight": info["solve_rate"]})
    return result


def plot_exclusive_outcomes(frame: pd.DataFrame):
    """Exclusive solves (right) and failures (left), block width weighted by task difficulty."""
    outcomes = exclusive_outcomes(frame)
    order = HARNESS_ORDER[::-1]
    fig, ax = plt.subplots(figsize=(6.3, 3.1))
    for i, harness in enumerate(order):
        color = HARNESS_COLORS[harness]
        for side, sign in (("solved", 1), ("failed", -1)):
            x = 0.0
            for item in sorted(outcomes[harness][side], key=lambda e: -e["weight"]):
                width = item["weight"] * sign
                ax.barh(i, width, left=x, height=0.62, linewidth=0.8,
                        color=color if sign > 0 else to_rgba(color, 0.12), edgecolor="white" if sign > 0 else color)
                ax.text(x + width / 2, i, f"{item['model']}\n{item['task']}\n{item['solve_rate']:.0%}",
                        ha="center", va="center", fontsize=5.3, color="white" if sign > 0 else INK)
                x += width
        net = sum(e["weight"] for e in outcomes[harness]["solved"]) - sum(e["weight"] for e in outcomes[harness]["failed"])
        ax.text(2.2, i, f"net {net:+.2f}", va="center", fontsize=7.5, weight="bold", color=INK)
    ax.axvline(0, color=INK, linewidth=0.9)
    ax.set_xlim(-1.7, 2.65)
    ax.set_yticks(range(len(order)), [HARNESS_LABELS[h] for h in order])
    ax.text(-0.85, len(order) - 0.35, "failed what all others solved", fontsize=7, color=MUTED, ha="center")
    ax.text(1.0, len(order) - 0.35, "solved what no other solved", fontsize=7, color=MUTED, ha="center")
    ax.set_xlabel("Difficulty-weighted tasks (solve: 1 − DeepSWE solve rate; failure: solve rate)", fontsize=7.5)
    _style_axis(ax, "x")
    ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    return fig


# --- Resource use -------------------------------------------------------------------------

def plot_cost_spread(frame: pd.DataFrame):
    """Mean cost per task of every harness, one column per model."""
    models = model_order(frame)
    cells = _cells(frame)
    offsets = {h: (i - 1.5) * 0.13 for i, h in enumerate(HARNESS_ORDER)}
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    _model_axis(ax, frame, models)
    for i, model in enumerate(models):
        cost = {h: cells[(model, h)]["cost_usd"].mean() for h in HARNESS_ORDER if (model, h) in cells}
        ax.add_patch(Rectangle((i - 0.3, min(cost.values())), 0.6, max(cost.values()) - min(cost.values()),
                               color="#e8e7e2", zorder=1))
        for harness, value in cost.items():
            ax.scatter(i + offsets[harness], value, color=HARNESS_COLORS[harness], marker=HARNESS_MARKERS[harness],
                       s=38, edgecolor="white", zorder=3)
        ax.text(i, max(cost.values()) * 1.3, f"{max(cost.values()) / min(cost.values()):.1f}× spread",
                ha="center", fontsize=6.8, color=MUTED)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"USD {v:g}"))
    ax.set_ylim(0.2, 50)
    ax.set_ylabel("Mean cost per task (log)", fontsize=8)
    _style_axis(ax)
    _harness_legend(ax, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return fig


def _lines_by_model(ax, frame, models, value, root_value=None, root_label=""):
    cells = _cells(frame)
    offsets = {h: (i - 1.5) * 0.09 for i, h in enumerate(HARNESS_ORDER)}
    for harness in HARNESS_ORDER:
        present = [m for m in models if (m, harness) in cells]
        ax.plot([models.index(m) + offsets[harness] for m in present], [value(cells[(m, harness)]) for m in present],
                color=HARNESS_COLORS[harness], marker=HARNESS_MARKERS[harness], markersize=6, linewidth=1.6,
                markeredgecolor="white", zorder=3)
    if root_value is not None:
        group = cells[("gpt-5.6-luna", "claude-code")]
        x, y = models.index("gpt-5.6-luna") + offsets["claude-code"], root_value(group)
        ax.scatter(x, y, marker=HARNESS_MARKERS["claude-code"], s=42, facecolor="white",
                   edgecolor=HARNESS_COLORS["claude-code"], linewidth=1.4, zorder=4)
        return x, y


def plot_cache_share(frame: pd.DataFrame):
    """Share of input tokens read from the cache; Claude Code on Luna also for its root session."""
    models = model_order(frame)
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    _model_axis(ax, frame, models)
    share = lambda g: 100 * g["cached_tokens"].sum() / g["input_tokens"].sum()
    root = lambda g: 100 * g["root_cached_tokens"].sum() / g["main_input_tokens"].sum()
    x, y = _lines_by_model(ax, frame, models, share, root)
    ax.annotate("Claude Code, root session\nwithout subagents", (x, y), xytext=(-110, -30), textcoords="offset points",
                fontsize=6.8, color=INK, arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.6})
    ax.set_ylim(86, 100)
    ax.set_ylabel("Input read from the cache (%)", fontsize=8)
    _style_axis(ax)
    _harness_legend(ax, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return fig


def plot_invocation_errors(frame: pd.DataFrame):
    """Share of tool calls rejected as invalid invocations; Claude Code on Luna also for its root session."""
    models = model_order(frame)
    fig, ax = plt.subplots(figsize=(6.3, 3.0))
    _model_axis(ax, frame, models)
    rate = lambda g: 100 * g["invocation_errors"].sum() / g["error_denominator"].sum()
    root = lambda g: 100 * g["root_invocation_errors"].sum() / g["root_counted_calls"].sum()
    x, y = _lines_by_model(ax, frame, models, rate, root)
    ax.annotate("Claude Code, root session", (x, y), xytext=(-30, 28), textcoords="offset points", fontsize=6.8,
                color=INK, arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.6})
    ax.set_ylim(0, None)
    ax.set_ylabel("Tool calls rejected as\ninvalid invocations (%)", fontsize=8)
    _style_axis(ax)
    _harness_legend(ax, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return fig


def plot_lost_tokens(frame: pd.DataFrame):
    """Share of each cell's input and output tokens spent in passed and in failed trials."""
    models = model_order(frame)
    cells = _cells(frame)
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    x = 0.0
    for model in models:
        start = x
        for harness in HARNESS_ORDER:
            if (model, harness) not in cells:
                continue
            group = cells[(model, harness)]
            tokens = group["input_tokens"] + group["output_tokens"]
            total, lost = tokens.sum(), tokens[~group["passed"]].sum()
            color = HARNESS_COLORS[harness]
            ax.bar(x, 100 * (total - lost) / total, color=color, width=0.72)
            ax.bar(x, 100 * lost / total, bottom=100 * (total - lost) / total, color="white", edgecolor=color,
                   hatch="////", width=0.72, linewidth=0.8)
            ax.text(x, 101.5, f"{total / len(group) / 1e6:.0f}M", ha="center", fontsize=6, color=MUTED)
            x += 1
        ax.text((start + x - 1) / 2, 112, MODELS[model][0], ha="center", fontsize=7, color=INK, weight="bold")
        x += 0.6
    ax.set_xticks([])
    ax.set_ylim(0, 118)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("Share of the tokens (%)", fontsize=8)
    _style_axis(ax)
    ax.legend(handles=[Patch(color=HARNESS_COLORS[h], label=HARNESS_LABELS[h]) for h in HARNESS_ORDER]
              + [Patch(facecolor="white", edgecolor=NEUTRAL, hatch="////", label="in failed trials")],
              frameon=False, fontsize=7, ncol=5, loc="upper left", bbox_to_anchor=(0, -0.02))
    fig.tight_layout()
    return fig


def failure_cost_ratios(frame: pd.DataFrame) -> dict[str, list[tuple[float, str]]]:
    """Cost of each failed trial relative to the median cost of the passing trials on the same model and task."""
    ratios = collections.defaultdict(list)
    for (model, _), group in frame.groupby(["model", "task"]):
        passed = group[group["passed"]]
        if passed.empty or len(passed) == len(group):
            continue
        reference = passed["cost_usd"].median()
        for _, row in group[~group["passed"]].iterrows():
            ratios[row["harness"]].append((row["cost_usd"] / reference, model))
    return ratios


def plot_failure_cost(frame: pd.DataFrame):
    """Failure cost relative to solving the same task; hatching shows the shift without Kimi K3."""
    ratios = failure_cost_ratios(frame)
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    order = HARNESS_ORDER[::-1]
    for i, harness in enumerate(order):
        color = HARNESS_COLORS[harness]
        values = ratios[harness]
        for j, (ratio, model) in enumerate(values):
            kimi = model == "kimi-k3"
            ax.scatter(math.log2(ratio), i + 0.12 + (j % 4 - 1.5) * 0.06, s=16, zorder=3, linewidth=0.9,
                       facecolor="white" if kimi else color, edgecolor=color, alpha=1 if kimi else 0.8)
        everything = statistics.mean(math.log2(r) for r, _ in values)
        without = [math.log2(r) for r, m in values if m != "kimi-k3"]
        if len(without) != len(values):
            shifted = statistics.mean(without)
            ax.add_patch(Rectangle((min(everything, shifted), i - 0.36), abs(shifted - everything), 0.16,
                                   facecolor="white", edgecolor=color, hatch="//////", linewidth=0.9, zorder=2))
        ax.plot([everything] * 2, [i - 0.4, i - 0.16], color=color, linewidth=3, solid_capstyle="butt", zorder=4)
    ax.axvline(0, color=MUTED, linewidth=0.8)
    ax.set_xlim(-5, 6)
    ax.set_xticks(range(-4, 6, 2), ["1/16×", "1/4×", "1×", "4×", "16×"])
    ax.set_yticks(range(len(order)), [HARNESS_LABELS[h] for h in order])
    ax.set_xlabel("Cost of a failed trial relative to the passing trials on the same model and task", fontsize=7.5)
    _style_axis(ax, "x")
    ax.tick_params(axis="y", length=0)
    ax.legend(handles=[Line2D([], [], color=NEUTRAL, linewidth=3, label="typical, all models"),
                       Patch(facecolor="white", edgecolor=NEUTRAL, hatch="//////", label="shift without Kimi K3"),
                       Line2D([], [], color=NEUTRAL, marker="o", markerfacecolor="white", linestyle="",
                              markersize=4, label="Kimi K3 failure")],
              frameon=False, fontsize=6.8, ncol=3, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return fig


def plot_prompt_and_tokens(frame: pd.DataFrame):
    """Harness prompt size (a) and tokens per task (b); hatching shows the change without Kimi K3."""
    medians = lambda h, column, models: statistics.median(
        frame[(frame["model"] == m) & (frame["harness"] == h)][column].median() for m in models
        if not frame[(frame["model"] == m) & (frame["harness"] == h)].empty)
    frame = frame.assign(total_tokens=frame["input_tokens"] + frame["output_tokens"])
    everything, core = model_order(frame), complete_models(frame)
    order = sorted(HARNESS_ORDER, key=lambda h: -medians(h, "first_prompt_tokens", everything))
    fig, (left, right) = plt.subplots(1, 2, figsize=(6.3, 2.7), sharey=True)
    for i, harness in enumerate(order):
        color = HARNESS_COLORS[harness]
        prompt = medians(harness, "first_prompt_tokens", everything) / 1e3
        left.barh(i, prompt, color=color, height=0.6)
        left.text(prompt + 0.3, i, f"{prompt:.1f}k", va="center", fontsize=7)
        with_kimi = medians(harness, "total_tokens", everything) / 1e6
        without = medians(harness, "total_tokens", core) / 1e6
        right.barh(i, min(with_kimi, without), color=color, height=0.6)
        if abs(with_kimi - without) > 0.05:
            right.barh(i, abs(without - with_kimi), left=min(with_kimi, without), color="white", edgecolor=color,
                       hatch="////", height=0.6, linewidth=0.9)
        right.text(max(with_kimi, without) + 0.6, i, f"{with_kimi:.0f}M" + (
            f" ({without:.0f}M without Kimi)" if abs(with_kimi - without) > 0.05 else ""), va="center", fontsize=7)
    left.set_yticks(range(len(order)), [HARNESS_LABELS[h] for h in order])
    left.set_xlim(0, 21)
    right.set_xlim(0, 52)
    left.set_xlabel("Harness prompt (thousand tokens)", fontsize=7.5)
    right.set_xlabel("Tokens per task (million, median over models)", fontsize=7.5)
    left.set_title("(a) System prompt and tool definitions", loc="left", fontsize=8)
    right.set_title("(b) Input and output tokens per task", loc="left", fontsize=8)
    for ax in (left, right):
        _style_axis(ax, "x")
        ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    return fig


# --- Tool interfaces ------------------------------------------------------------------------

def plot_actions_per_step(frame: pd.DataFrame):
    """Codex cells with more than one tool call against parallel tool calls in the other harnesses."""
    models = model_order(frame)
    cells = _cells(frame)
    offsets = {h: (i - 1.5) * 0.13 for i, h in enumerate(HARNESS_ORDER)}
    fig, ax = plt.subplots(figsize=(6.3, 3.0))
    for harness in HARNESS_ORDER:
        present = [m for m in models if (m, harness) in cells]
        values = []
        for model in present:
            group = cells[(model, harness)]
            values.append(100 * (group["codex_multi_op_cells"].sum() / group["codex_cells"].sum() if harness == "codex"
                                 else group["multi_call_steps"].sum() / group["steps_with_calls"].sum()))
        ax.bar([models.index(m) + offsets[harness] for m in present], values, width=0.12, color=HARNESS_COLORS[harness],
               label=HARNESS_LABELS[harness])
    ax.set_xticks(range(len(models)), [MODELS[m][0] for m in models], fontsize=7.5)
    ax.set_ylabel("Steps with more than one tool call (%)", fontsize=8)
    _style_axis(ax)
    ax.legend(frameon=False, fontsize=7, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0))
    fig.tight_layout()
    return fig


def plot_pi_timeouts(frame: pd.DataFrame):
    """Share of Pi's shell calls with a model-set timeout, and the trials that hung until the time limit."""
    models = model_order(frame)
    pi = frame[frame["harness"] == "pi"]
    fig, ax = plt.subplots(figsize=(6.3, 2.8))
    for i, model in enumerate(models):
        group = pi[pi["model"] == model]
        share = 100 * group["shell_calls_with_timeout"].sum() / group["shell_calls"].sum()
        ax.bar(i, share, color=HARNESS_COLORS["pi"], width=0.55)
        ax.text(i, share + 2, f"{share:.0f}%", ha="center", fontsize=7.5)
        hung = int(group["timeout"].sum())
        if hung:
            ax.text(i, share + 10, f"{hung} trial{'s' if hung > 1 else ''} hung\nuntil the time limit",
                    ha="center", fontsize=6.8, color="#b00020")
    ax.set_xticks(range(len(models)), [MODELS[m][0] for m in models], fontsize=7.5)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Pi shell calls with a\nmodel-set timeout (%)", fontsize=8)
    _style_axis(ax)
    fig.tight_layout()
    return fig


# --- Numbers quoted in the text ------------------------------------------------------------

def _trajectories(frame: pd.DataFrame):
    for _, row in frame.iterrows():
        jobs = MODELS[row["model"]][1]
        path = next(job / row["trial"] / "agent" / "trajectory.json" for job in jobs if (job / row["trial"]).exists())
        yield row, json.loads(path.read_text())


def shell_safeguards(frame: pd.DataFrame) -> dict[str, Any]:
    """Background launches, commands stopped by Claude Code's default limit, and Codex's polling runs."""
    background = collections.Counter()
    default_kills = 0
    longest_poll = 0
    for row, trajectory in _trajectories(frame):
        streak, previous = 0, None
        for step in trajectory.get("steps", []):
            if step.get("source") != "agent":
                continue
            observations = {r.get("source_call_id"): str(r.get("content"))
                            for r in (step.get("observation") or {}).get("results") or []}
            for call in step.get("tool_calls") or []:
                arguments = call.get("arguments") or {}
                if call.get("function_name") in SHELL_TOOLS:
                    background[row["harness"]] += bool(arguments.get("run_in_background") or arguments.get("background"))
                if (row["harness"] == "claude-code" and call.get("function_name") == "Bash" and "timeout" not in arguments
                        and "Command timed out after 2m" in observations.get(call.get("tool_call_id"), "")):
                    default_kills += 1
                if row["harness"] == "codex":
                    source = arguments.get("input", "")
                    poll = "write_stdin" in source and re.search(r'chars:\s*""', source)
                    streak = streak + 1 if poll and source == previous else (1 if poll else 0)
                    previous = source
                    longest_poll = max(longest_poll, streak)
    return {"background": {h: background[h] for h in HARNESS_ORDER}, "claude_code_default_kills": default_kills,
            "codex_longest_poll_run": longest_poll}


def opencode_search_calls(frame: pd.DataFrame) -> dict[str, int]:
    """OpenCode V2's native grep and glob calls, which all depended on the missing ripgrep binary."""
    calls = trials = 0
    for row, trajectory in _trajectories(frame[frame["harness"] == "opencode-v2"]):
        found = sum(call.get("function_name") in OPENCODE_RIPGREP_TOOLS
                    for step in trajectory.get("steps", []) for call in step.get("tool_calls") or [])
        calls += found
        trials += found > 0
    return {"calls": calls, "trials_with_calls": trials, "trials": int((frame["harness"] == "opencode-v2").sum())}
