"""Reusable plotting helpers for figures rendered from Quarto.

The helpers intentionally contain presentation logic only. Research data stays in
`data/`; result calculations should remain explicit in the corresponding
analysis cell or analysis script.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import matplotlib.pyplot as plt
import pandas as pd


def plot_pareto_frontier(
    data: pd.DataFrame,
    *,
    cost_col: str = "cost",
    performance_col: str = "success_rate",
    label_col: str | None = None,
):
    """Plot cost versus performance and draw the non-dominated frontier.

    Lower cost and higher performance are treated as better. Rows with missing
    values in either plotted metric are ignored.

    Returns the Matplotlib Axes so the Quarto cell can make figure-specific
    adjustments when needed.
    """
    frame = (
        data.dropna(subset=[cost_col, performance_col])
        .sort_values([cost_col, performance_col], ascending=[True, False])
        .copy()
    )

    fig, ax = plt.subplots()
    ax.scatter(frame[cost_col], frame[performance_col])

    frontier_rows = []
    best_performance = float("-inf")
    for _, row in frame.iterrows():
        performance = float(row[performance_col])
        if performance > best_performance:
            frontier_rows.append(row)
            best_performance = performance

    if frontier_rows:
        frontier = pd.DataFrame(frontier_rows)
        ax.plot(frontier[cost_col], frontier[performance_col])

    if label_col is not None:
        for _, row in frame.iterrows():
            ax.annotate(
                str(row[label_col]),
                (row[cost_col], row[performance_col]),
                xytext=(4, 4),
                textcoords="offset points",
            )

    ax.set_xlabel("Cost per task (USD)")
    ax.set_ylabel("Task success rate")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    return ax


def plot_grouped_line(
    data: pd.DataFrame,
    *,
    x_col: str,
    y_col: str,
    group_col: str,
    x_order: Sequence | None = None,
    x_label: str | None = None,
    y_label: str | None = None,
):
    """Plot one line per group over an ordered categorical x-axis.

    `x_order` should be supplied when the paper defines a specific model order,
    for example increasing model cost.
    """
    frame = data.dropna(subset=[x_col, y_col, group_col]).copy()

    if x_order is None:
        x_order = list(dict.fromkeys(frame[x_col].tolist()))
    else:
        x_order = list(x_order)

    position = {value: index for index, value in enumerate(x_order)}

    fig, ax = plt.subplots()
    for group, group_frame in frame.groupby(group_col, sort=False):
        ordered = group_frame[group_frame[x_col].isin(position)].copy()
        ordered["_x_position"] = ordered[x_col].map(position)
        ordered = ordered.sort_values("_x_position")
        ax.plot(
            ordered["_x_position"],
            ordered[y_col],
            marker="o",
            label=str(group),
        )

    ax.set_xticks(range(len(x_order)), [str(value) for value in x_order])
    ax.set_xlabel(x_label or x_col)
    ax.set_ylabel(y_label or y_col)
    ax.grid(alpha=0.2)
    ax.legend()
    fig.tight_layout()
    return ax
