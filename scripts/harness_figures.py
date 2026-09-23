"""Plotting helpers for the per-model harness comparisons in the results chapter.

Every model section uses the same harness colors, marker shapes, and task order,
so a harness looks the same in every figure. The data frames come from
`scripts/analyze_harness_trials.py` (one row per canonical trial); the helpers
contain presentation logic only.

Harness colors follow the tools' own identities: Codex black, Pi blue, Claude
Code orange, and OpenCode V2 terminal green. Orange and green are close for
readers with red-green color vision deficiency, so each harness also has its own
marker shape, and harness names are always written on the axis or next to the
mark.
"""

from __future__ import annotations

from collections.abc import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

from scripts.analyze_harness_trials import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    HARNESS_LABELS,
    HARNESS_ORDER,
    task_catalog,
)

HARNESS_COLORS = {
    "codex": "#1a1a1a",
    "pi": "#2a78d6",
    "claude-code": "#d97757",
    "opencode-v2": "#0f7b32",
}
HARNESS_MARKERS = {"codex": "s", "pi": "o", "claude-code": "D", "opencode-v2": "^"}
# Tool-call categories; hues differ from the harness colors.
CATEGORY_COLORS = {
    "read": "#4a3aa7",
    "edit": "#1baf7a",
    "shell": "#e87ba4",
    "delegation": "#eda100",
    "planning": "#e34948",
    "other": "#b4b2a9",
}
SHARE_CMAP = LinearSegmentedColormap.from_list(
    "f2p_share", ["#f1f0ec", "#9ec5f4", "#3987e5", "#1c5cab", "#0d366b"]
)
INK = "#1a1a19"
MUTED = "#6f6e69"
GRID = "#e4e3de"


def harnesses_in(frame: pd.DataFrame) -> list[str]:
    """Harnesses present in the frame, in the fixed chapter order."""
    present = set(frame["harness"])
    return [harness for harness in HARNESS_ORDER if harness in present]


def _style_axis(ax, grid_axis: str = "y") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=8)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_task_outcomes(frame: pd.DataFrame, ax=None):
    """Task-by-harness matrix of fail-to-pass tests passed; passing trials get a check mark.

    A cell whose fail-to-pass tests all pass but which still failed broke
    previously passing tests and is marked "regr.".
    """
    harnesses = harnesses_in(frame)
    catalog = [task for task in task_catalog() if task["task"] in set(frame["task"])]
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 0.42 * len(catalog) + 1.2))

    shares = np.full((len(catalog), len(harnesses)), np.nan)
    for i, task in enumerate(catalog):
        for j, harness in enumerate(harnesses):
            row = frame[(frame["task"] == task["task"]) & (frame["harness"] == harness)]
            if row.empty:
                continue
            row = row.iloc[0]
            total = row["f2p_total"] or 0
            passed = row["f2p_passed"] or 0
            share = passed / total if total else 0.0
            shares[i, j] = share
            if pd.isna(row["f2p_total"]):
                label = "no result"
            else:
                label = f"{int(passed)}/{int(total)}"
                if row["passed"]:
                    label = "✓ " + label
                elif share == 1:
                    label += " regr."
            ax.text(j, i, label, ha="center", va="center", fontsize=7.5,
                    color="white" if share >= 0.55 else INK,
                    fontweight="bold" if row["passed"] else "normal")

    image = ax.imshow(shares, cmap=SHARE_CMAP, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(harnesses)), [HARNESS_LABELS[h] for h in harnesses])
    ax.set_yticks(range(len(catalog)), [task["label"] for task in catalog])
    ax.xaxis.tick_top()
    ax.tick_params(length=0, labelsize=8, colors=INK)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(harnesses)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(catalog)), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    hard = sum(task["stratum"] == "hard" for task in catalog)
    if 0 < hard < len(catalog):
        ax.axhline(hard - 0.5, color=INK, linewidth=1.2)
        for label, middle in (("hard", (hard - 1) / 2), ("medium", (hard + len(catalog) - 1) / 2)):
            ax.text(len(harnesses) - 0.4, middle, label, rotation=270, va="center",
                    fontsize=7.5, color=MUTED)
    colorbar = ax.figure.colorbar(image, ax=ax, orientation="horizontal", fraction=0.04,
                                  pad=0.03, aspect=40)
    colorbar.set_label("Share of fail-to-pass tests passed", fontsize=8)
    colorbar.ax.tick_params(labelsize=7)
    colorbar.outline.set_visible(False)
    return ax


def plot_trial_strip(
    frame: pd.DataFrame,
    value_col: str,
    *,
    ax,
    scale: float = 1.0,
    log: bool = False,
    ylabel: str | None = None,
    reference: float | None = None,
    reference_label: str | None = None,
):
    """One dot per trial for each harness, with the median as a horizontal bar.

    Passing trials are filled and failing trials hollow.
    """
    harnesses = harnesses_in(frame)
    rng = np.random.default_rng(7)
    for position, harness in enumerate(harnesses):
        group = frame[frame["harness"] == harness]
        values = group[value_col].to_numpy(dtype=float) / scale
        offsets = rng.uniform(-0.18, 0.18, len(values))
        color = HARNESS_COLORS[harness]
        for offset, value, passed in zip(offsets, values, group["passed"], strict=True):
            ax.scatter(position + offset, value, s=30, marker=HARNESS_MARKERS[harness],
                       facecolor=color if passed else "white", edgecolor=color,
                       linewidth=1.3, zorder=3)
        median = float(np.median(values))
        ax.plot([position - 0.3, position + 0.3], [median, median], color=INK,
                linewidth=2, solid_capstyle="round", zorder=4)
    if reference is not None:
        ax.axhline(reference, color=MUTED, linewidth=1, linestyle="--", zorder=2)
        if reference_label:
            ax.text(len(harnesses) - 0.5, reference, reference_label, ha="right",
                    va="bottom", fontsize=7, color=MUTED)
    if log:
        ax.set_yscale("log")
    ax.set_xticks(range(len(harnesses)), [HARNESS_LABELS[h] for h in harnesses])
    ax.set_xlim(-0.6, len(harnesses) - 0.4)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8)
    _style_axis(ax)
    return ax


def outcome_legend(ax, **kwargs) -> None:
    """Legend explaining filled (passed) and hollow (failed) trial markers."""
    color = MUTED
    handles = [
        plt.Line2D([], [], marker="o", linestyle="", markerfacecolor=color,
                   markeredgecolor=color, label="passed"),
        plt.Line2D([], [], marker="o", linestyle="", markerfacecolor="white",
                   markeredgecolor=color, label="failed"),
        plt.Line2D([], [], color=INK, linewidth=2, label="median"),
    ]
    ax.legend(handles=handles, frameon=False, fontsize=7, **kwargs)


def plot_task_costs(frame: pd.DataFrame, ax=None, value_col: str = "cost_usd"):
    """Grouped bars of the normalized cost per task, one bar per harness.

    The cost axis is logarithmic. A check mark above a bar denotes a passing
    trial; failing trials are drawn as hollow bars.
    """
    harnesses = harnesses_in(frame)
    catalog = [task for task in task_catalog() if task["task"] in set(frame["task"])]
    pivot = frame.pivot(index="task", columns="harness", values=value_col)
    passed = frame.pivot(index="task", columns="harness", values="passed")
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 3.4))
    width = 0.8 / len(harnesses)
    for offset, harness in enumerate(harnesses):
        color = HARNESS_COLORS[harness]
        for position, task in enumerate(catalog):
            value = pivot.loc[task["task"], harness]
            ok = bool(passed.loc[task["task"], harness])
            x = position - 0.4 + width * (offset + 0.5)
            ax.bar(x, value, width=width * 0.9, color=color if ok else "white",
                   edgecolor=color, linewidth=1.1, zorder=3)
            if ok:
                ax.text(x, value * 1.15, "\u2713", ha="center", va="bottom",
                        fontsize=7, color=INK, zorder=4)
    ax.set_yscale("log")
    ax.set_ylim(pivot.min().min() / 2, pivot.max().max() * 3)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: f"{value:g}"))
    hard = sum(task["stratum"] == "hard" for task in catalog)
    if 0 < hard < len(catalog):
        ax.axvline(hard - 0.5, color=MUTED, linewidth=0.8, linestyle=":")
        for label, middle in (("hard", (hard - 1) / 2), ("medium", (hard + len(catalog) - 1) / 2)):
            ax.text(middle, pivot.max().max() * 2.4, label, ha="center", va="top",
                    fontsize=7, color=MUTED)
    ax.set_xticks(range(len(catalog)), [task["label"].split(" (")[0] for task in catalog],
                  rotation=35, ha="right")
    ax.set_xlim(-0.6, len(catalog) - 0.4)
    ax.set_ylabel("Normalized cost of the trial (USD, log scale)", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=HARNESS_COLORS[h]) for h in harnesses]
    handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="white", edgecolor=MUTED))
    ax.legend(handles, [HARNESS_LABELS[h] for h in harnesses] + ["failed (hollow)"],
              loc="lower center", bbox_to_anchor=(0.5, 1.0), ncols=len(handles),
              frameon=False, fontsize=7, handlelength=1.2, columnspacing=1.0)
    _style_axis(ax)
    return ax


def plot_tool_mix(frame: pd.DataFrame, ax=None):
    """Share of tool calls by category per harness, with the invocation-error rate."""
    harnesses = harnesses_in(frame)
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 0.55 * len(harnesses) + 1.1))
    for position, harness in enumerate(harnesses):
        group = frame[frame["harness"] == harness]
        counts = {c: sum(row[c] for row in group["calls_by_category"]) for c in CATEGORY_ORDER}
        total = sum(counts.values())
        left = 0.0
        for category in CATEGORY_ORDER:
            share = counts[category] / total
            if share == 0:
                continue
            ax.barh(position, share, left=left, height=0.62, color=CATEGORY_COLORS[category],
                    edgecolor="white", linewidth=1.5)
            if share >= 0.07:
                ax.text(left + share / 2, position, f"{share:.0%}", ha="center", va="center",
                        fontsize=7, color="white" if category in {"read", "planning"} else INK)
            left += share
        errors = group["invocation_errors"].sum() / group["error_denominator"].sum()
        ax.text(1.02, position, f"{total:,} calls\n{errors:.1%} call errors", va="center",
                fontsize=7, color=INK)
    ax.set_yticks(range(len(harnesses)), [HARNESS_LABELS[h] for h in harnesses])
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: f"{value:.0%}"))
    ax.set_xlabel("Share of tool calls", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=CATEGORY_COLORS[c]) for c in CATEGORY_ORDER]
    ax.legend(handles, [CATEGORY_LABELS[c] for c in CATEGORY_ORDER], loc="lower center",
              bbox_to_anchor=(0.5, 1.0), ncols=len(CATEGORY_ORDER), frameon=False,
              fontsize=7, handlelength=1, columnspacing=1)
    _style_axis(ax, grid_axis="x")
    return ax


def plot_subagent_tokens(frame: pd.DataFrame, harness: str = "claude-code", ax=None):
    """Input tokens of the root session and of its subagents per task for one harness.

    The number after each bar is the count of subagents spawned; a check mark
    marks passing trials.
    """
    catalog = task_catalog()
    group = frame[frame["harness"] == harness].set_index("task")
    order = [task for task in catalog if task["task"] in group.index]
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 0.4 * len(order) + 1))
    color = HARNESS_COLORS[harness]
    for position, task in enumerate(order):
        row = group.loc[task["task"]]
        main = row["main_input_tokens"] / 1e6
        sub = row["subagent_input_tokens"] / 1e6
        ax.barh(position, main, height=0.62, color=color, edgecolor="white", linewidth=1.5)
        ax.barh(position, sub, left=main, height=0.62, color=color, alpha=0.4,
                edgecolor="white", linewidth=1.5, hatch="////")
        mark = "✓ " if row["passed"] else ""
        ax.text(main + sub + 8, position, f"{mark}{int(row['subagent_spawns'])} subagents",
                va="center", fontsize=7, color=INK)
    ax.set_yticks(range(len(order)), [task["label"] for task in order])
    ax.invert_yaxis()
    ax.set_xlabel("Input tokens (million)", fontsize=8)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=color),
        plt.Rectangle((0, 0), 1, 1, facecolor=color, alpha=0.4, hatch="////", edgecolor="white"),
    ]
    ax.legend(handles, ["root session", "subagents"], frameon=False, fontsize=7,
              loc="upper center", bbox_to_anchor=(0.5, -0.16), ncols=2)
    ax.set_xlim(0, ax.get_xlim()[1] * 1.18)
    _style_axis(ax, grid_axis="x")
    return ax


def plot_paired_metric(
    frame: pd.DataFrame,
    value_col: str,
    *,
    harness: str,
    reference: str,
    ax,
    label: str,
):
    """Per-task comparison of two harnesses on log axes, with the parity line.

    Points above the line are tasks on which `harness` used more than
    `reference`. Filled markers are tasks that `harness` passed.
    """
    pivot = frame.pivot(index="task", columns="harness", values=value_col)
    passed = frame[frame["harness"] == harness].set_index("task")["passed"]
    color = HARNESS_COLORS[harness]
    for task, row in pivot.iterrows():
        ax.scatter(row[reference], row[harness], s=36, marker=HARNESS_MARKERS[harness],
                   facecolor=color if passed[task] else "white", edgecolor=color,
                   linewidth=1.3, zorder=3)
    low = min(pivot[reference].min(), pivot[harness].min()) / 1.5
    high = max(pivot[reference].max(), pivot[harness].max()) * 1.5
    ax.plot([low, high], [low, high], color=MUTED, linewidth=1, linestyle="--", zorder=2)
    ax.text(high / 1.2, high / 1.2, "parity", rotation=45, ha="right", va="bottom",
            fontsize=7, color=MUTED, rotation_mode="anchor")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(low, high)
    ax.set_ylim(low, high)
    ax.set_aspect("equal")
    ax.set_xlabel(f"{HARNESS_LABELS[reference]}: {label}", fontsize=8)
    ax.set_ylabel(f"{HARNESS_LABELS[harness]}: {label}", fontsize=8)
    _style_axis(ax, grid_axis="both")
    return ax


__all__: Sequence[str] = [
    "HARNESS_COLORS",
    "HARNESS_MARKERS",
    "harnesses_in",
    "outcome_legend",
    "plot_paired_metric",
    "plot_subagent_tokens",
    "plot_task_costs",
    "plot_task_outcomes",
    "plot_tool_mix",
    "plot_trial_strip",
]
