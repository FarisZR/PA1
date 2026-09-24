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
from matplotlib.ticker import LogLocator, NullFormatter

from scripts.analyze_harness_trials import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    HARNESS_LABELS,
    HARNESS_ORDER,
    pareto_front,
    summarize,
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
    "planning": "#008300",
    "other": "#b4b2a9",
}
SHARE_CMAP = LinearSegmentedColormap.from_list(
    "f2p_share", ["#f1f0ec", "#9ec5f4", "#3987e5", "#1c5cab", "#0d366b"]
)
INK = "#1a1a19"
MUTED = "#6f6e69"
GRID = "#e4e3de"
PASSED_COLOR = "#008300"


def harnesses_in(frame: pd.DataFrame) -> list[str]:
    """Harnesses present in the frame, in the fixed chapter order."""
    present = set(frame["harness"])
    return [harness for harness in HARNESS_ORDER if harness in present]


def _groups(frame: pd.DataFrame, column: str, order: Sequence[str] | None) -> list[str]:
    """Groups of ``column`` to draw: the given order, or the harnesses in chapter order."""
    if order is None:
        return harnesses_in(frame)
    return [group for group in order if group in set(frame[column])]


def _style_axis(ax, grid_axis: str = "y") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=8)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_task_outcomes(
    frame: pd.DataFrame,
    ax=None,
    *,
    column: str = "harness",
    order: Sequence[str] | None = None,
    labels: dict[str, str] | None = None,
):
    """Task-by-harness matrix of fail-to-pass tests passed; passing trials are green.

    Failing trials are shaded blue by their share of fail-to-pass tests passed.
    A failing cell whose fail-to-pass tests all pass broke previously passing
    tests and is marked "regr.". ``column``, ``order``, and ``labels`` select
    other columns than the harness, for example runs of the same harness.
    """
    harnesses = _groups(frame, column, order)
    labels = labels or HARNESS_LABELS
    catalog = [task for task in task_catalog() if task["task"] in set(frame["task"])]
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 0.42 * len(catalog) + 1.2))

    shares = np.full((len(catalog), len(harnesses)), np.nan)
    passed_cells = np.zeros((len(catalog), len(harnesses)), dtype=bool)
    for i, task in enumerate(catalog):
        for j, harness in enumerate(harnesses):
            row = frame[(frame["task"] == task["task"]) & (frame[column] == harness)]
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
                    passed_cells[i, j] = True
                elif share == 1:
                    label += " regr."
            ax.text(j, i, label, ha="center", va="center", fontsize=7.5,
                    color="white" if share >= 0.55 else INK,
                    fontweight="bold" if row["passed"] else "normal")

    image = ax.imshow(np.where(passed_cells, np.nan, shares), cmap=SHARE_CMAP, vmin=0, vmax=1,
                      aspect="auto")
    ax.imshow(np.where(passed_cells, 1.0, np.nan),
              cmap=LinearSegmentedColormap.from_list("passed", [PASSED_COLOR, PASSED_COLOR]),
              vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(harnesses)), [labels[h] for h in harnesses])
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
    colorbar.ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=PASSED_COLOR)],
                       labels=["passed trial"], loc="center left", bbox_to_anchor=(1.01, 0.5),
                       frameon=False, fontsize=7.5, handlelength=1.2)
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
    column: str = "harness",
    order: Sequence[str] | None = None,
    labels: dict[str, str] | None = None,
    colors: dict[str, str] | None = None,
    markers: dict[str, str] | None = None,
):
    """One dot per trial for each harness, with the median as a horizontal bar.

    Passing trials are filled and failing trials hollow. ``column`` and the
    optional mappings select other groups than the harness.
    """
    harnesses = _groups(frame, column, order)
    labels = labels or HARNESS_LABELS
    colors = colors or HARNESS_COLORS
    markers = markers or HARNESS_MARKERS
    rng = np.random.default_rng(7)
    for position, harness in enumerate(harnesses):
        group = frame[frame[column] == harness]
        values = group[value_col].to_numpy(dtype=float) / scale
        offsets = rng.uniform(-0.18, 0.18, len(values))
        color = colors[harness]
        for offset, value, passed in zip(offsets, values, group["passed"], strict=True):
            ax.scatter(position + offset, value, s=30, marker=markers[harness],
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
    ax.set_xticks(range(len(harnesses)), [labels[h] for h in harnesses])
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
    trial; failing trials are drawn as hatched bars.
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
            ax.bar(x, value, width=width * 0.9, facecolor=color if ok else "white",
                   edgecolor=color, hatch=None if ok else "//////", linewidth=1.1, zorder=3)
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
    handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="white", edgecolor=MUTED, hatch="//////"))
    ax.legend(handles, [HARNESS_LABELS[h] for h in harnesses] + ["failed (hatched)"],
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
        ax.text(1.02, position, f"{total:,} calls", va="center",
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


def plot_cache_tokens(frame: pd.DataFrame, ax=None):
    """Input tokens against cache-hit share, one point per trial.

    Points of the same harness are joined in order of their input tokens. Few
    tokens and a high cache-hit share (top left) is best. Filled markers are
    passing trials. The token axis is logarithmic.
    """
    harnesses = harnesses_in(frame)
    data = frame.assign(cache_share=frame["cached_tokens"] / frame["input_tokens"],
                        input_m=frame["input_tokens"] / 1e6)
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 3.4))
    for harness in harnesses:
        group = data[data["harness"] == harness].sort_values("input_m")
        color = HARNESS_COLORS[harness]
        ax.plot(group["input_m"], group["cache_share"], color=color, linewidth=1, alpha=0.7,
                zorder=2)
        for _, row in group.iterrows():
            ax.scatter(row["input_m"], row["cache_share"], s=32, marker=HARNESS_MARKERS[harness],
                       facecolor=color if row["passed"] else "white", edgecolor=color,
                       linewidth=1.3, zorder=3)
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: f"{value:g}"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: f"{value:.0%}"))
    ax.set_xlabel("Input tokens per trial (million, log scale)", fontsize=8)
    ax.set_ylabel("Input tokens read from the cache", fontsize=8)
    handles = [
        plt.Line2D([], [], color=HARNESS_COLORS[h], marker=HARNESS_MARKERS[h], linewidth=1,
                   markersize=5, label=HARNESS_LABELS[h])
        for h in harnesses
    ]
    handles.append(plt.Line2D([], [], marker="o", linestyle="", markersize=5,
                              markerfacecolor="white", markeredgecolor=MUTED,
                              label="failed (hollow)"))
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              ncols=len(handles), frameon=False, fontsize=7, columnspacing=1)
    _style_axis(ax, grid_axis="both")
    return ax


def plot_invocation_errors(frame: pd.DataFrame, ax=None):
    """Invocation-error rate per harness, stacked by the tool category of the rejected calls.

    The bar height is the share of all counted calls rejected as invalid; each
    segment is one tool category, labeled with its share of the harness's errors.
    Category colors match `plot_tool_mix`. The y-axis starts at zero but ends just
    above the highest rate, so small differences stay visible.
    """
    harnesses = harnesses_in(frame)
    if ax is None:
        _, ax = plt.subplots(figsize=(6.3, 3.2))
    used: set[str] = set()
    top = 0.0
    for position, harness in enumerate(harnesses):
        group = frame[frame["harness"] == harness]
        calls = int(group["error_denominator"].sum())
        by_category = {c: sum(row[c] for row in group["errors_by_category"]) for c in CATEGORY_ORDER}
        errors = sum(by_category.values())
        bottom = 0.0
        for category in CATEGORY_ORDER:
            count = by_category[category]
            if count == 0:
                continue
            used.add(category)
            height = count / calls
            ax.bar(position, height, bottom=bottom, width=0.62, color=CATEGORY_COLORS[category],
                   edgecolor="white", linewidth=1.5, zorder=3)
            share = count / errors
            if height >= 0.0025:
                ax.text(position, bottom + height / 2, f"{share:.0%}", ha="center", va="center",
                        fontsize=7, color="white" if category in {"read", "planning"} else INK,
                        zorder=4)
            bottom += height
        top = max(top, bottom)
        ax.annotate(f"{bottom:.1%}\n{errors:,} of {calls:,}", (position, bottom), xytext=(0, 3),
                    textcoords="offset points", ha="center", va="bottom", fontsize=7.5,
                    color=INK, fontweight="bold")
    ax.set_xticks(range(len(harnesses)), [HARNESS_LABELS[h] for h in harnesses])
    ax.set_xlim(-0.6, len(harnesses) - 0.4)
    ax.set_ylim(0, top * 1.25)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: f"{value:.1%}"))
    ax.set_ylabel("Tool calls rejected as invalid", fontsize=8)
    order = [c for c in CATEGORY_ORDER if c in used]
    handles = [plt.Rectangle((0, 0), 1, 1, color=CATEGORY_COLORS[c]) for c in order]
    ax.legend(handles, [CATEGORY_LABELS[c] for c in order], loc="lower center",
              bbox_to_anchor=(0.5, 1.0), ncols=len(order), frameon=False, fontsize=7,
              handlelength=1, columnspacing=1)
    _style_axis(ax)
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
        ax.annotate(f"{mark}{int(row['subagent_spawns'])} subagents", (main + sub, position),
                    xytext=(4, 0), textcoords="offset points", va="center", fontsize=7,
                    color=INK)
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


__all__: Sequence[str] = [
    "HARNESS_COLORS",
    "HARNESS_MARKERS",
    "harnesses_in",
    "outcome_legend",
    "plot_cache_tokens",
    "plot_invocation_errors",
    "plot_subagent_tokens",
    "plot_task_costs",
    "plot_task_outcomes",
    "plot_tool_mix",
    "plot_trial_strip",
]


# Label placement per harness, so that harnesses with equal success do not overlap.
_PARETO_LABEL_OFFSETS = {
    "codex": (6, 5, "left"),
    "pi": (6, -11, "left"),
    "claude-code": (-6, 5, "right"),
    "opencode-v2": (-6, -11, "right"),
}


def plot_pareto(frame: pd.DataFrame, axes=None):
    """Passed tasks against total cost (a) and total tokens (b), one point per harness.

    Harnesses on the Pareto front are filled and joined by a line; dominated
    harnesses are hollow. Both horizontal axes are logarithmic.
    """
    summary = summarize(frame.to_dict("records"))
    if axes is None:
        _, axes = plt.subplots(1, 2, figsize=(6.3, 2.9), sharey=True)
    panels = (
        ("cost_usd", 1.0, "(a) Normalized cost of ten trials (USD)"),
        ("total_tokens", 1e6, "(b) Input and output tokens of ten trials (million)"),
    )
    for ax, (key, scale, xlabel) in zip(axes, panels, strict=True):
        front = pareto_front(summary, key)
        line = sorted((summary[h][key] / scale, summary[h]["passed"]) for h in front)
        ax.plot([x for x, _ in line], [y for _, y in line], color=MUTED, linewidth=1,
                linestyle="--", zorder=2)
        for harness, stats in summary.items():
            color = HARNESS_COLORS[harness]
            x = stats[key] / scale
            ax.scatter(x, stats["passed"], s=55, marker=HARNESS_MARKERS[harness],
                       facecolor=color if harness in front else "white", edgecolor=color,
                       linewidth=1.4, zorder=3)
            dx, dy, ha = _PARETO_LABEL_OFFSETS[harness]
            ax.annotate(HARNESS_LABELS[harness], (x, stats["passed"]), xytext=(dx, dy),
                        textcoords="offset points", fontsize=7, color=color, ha=ha)
        ax.set_xscale("log")
        values = [stats[key] / scale for stats in summary.values()]
        ax.set_xlim(min(values) / 1.6, max(values) * 2.2)
        ax.xaxis.set_major_locator(LogLocator(subs=(1.0, 2.0, 5.0)))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: f"{value:g}"))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_xlabel(xlabel, fontsize=8)
        _style_axis(ax, grid_axis="both")
    axes[0].set_ylim(-0.5, 10.5)
    axes[0].set_yticks(range(0, 11, 2))
    axes[0].set_ylabel("Tasks passed (of 10)", fontsize=8)
    axes[1].legend(
        handles=[plt.Line2D([], [], marker="o", color=MUTED, linestyle="--", markerfacecolor=MUTED),
                 plt.Line2D([], [], marker="o", color=MUTED, linestyle="", markerfacecolor="white")],
        labels=["Pareto front", "dominated"], loc="lower right", fontsize=7, frameon=False,
    )
    return axes
