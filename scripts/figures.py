"""Reusable plotting helpers for figures rendered from Quarto.

The helpers intentionally contain presentation logic only. Research data stays in
`data/`; result calculations should remain explicit in the corresponding
analysis cell or analysis script.
"""

from __future__ import annotations

from collections.abc import Sequence

import matplotlib.pyplot as plt
import pandas as pd


def _place_legend_above(ax, labels: Sequence[str]) -> None:
    if len(labels) > 1:
        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.01),
            ncols=min(len(labels), 3),
            frameon=False,
        )


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


def plot_grouped_bar(
    data: pd.DataFrame,
    *,
    category_col: str,
    value_cols: Sequence[str],
    series_labels: Sequence[str] | None = None,
    horizontal: bool = False,
    x_label: str | None = None,
    y_label: str | None = None,
    percentage_axis: bool = False,
    annotate: bool = False,
):
    """Plot side-by-side bars for one or more numeric series."""
    frame = data[[category_col, *value_cols]].copy()
    labels = list(series_labels or value_cols)
    if len(labels) != len(value_cols):
        raise ValueError("series_labels must match value_cols")

    fig, ax = plt.subplots()
    positions = list(range(len(frame)))
    series_count = len(value_cols)
    width = 0.8 / max(series_count, 1)
    offsets = [
        (index - (series_count - 1) / 2) * width
        for index in range(series_count)
    ]

    for offset, column, label in zip(offsets, value_cols, labels, strict=True):
        values = frame[column].astype(float)
        shifted = [position + offset for position in positions]
        if horizontal:
            bars = ax.barh(shifted, values, height=width * 0.92, label=label)
        else:
            bars = ax.bar(shifted, values, width=width * 0.92, label=label)

        if annotate:
            for bar, value in zip(bars, values, strict=True):
                if horizontal:
                    ax.text(
                        value + 1,
                        bar.get_y() + bar.get_height() / 2,
                        f"{value:.1f}%" if percentage_axis else f"{value:g}",
                        va="center",
                        fontsize=8,
                    )
                else:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        value + 1,
                        f"{value:.1f}%" if percentage_axis else f"{value:g}",
                        ha="center",
                        fontsize=8,
                    )

    categories = frame[category_col].astype(str).tolist()
    if horizontal:
        ax.set_yticks(positions, categories)
        ax.invert_yaxis()
        ax.set_xlabel(x_label or "Value")
        if y_label:
            ax.set_ylabel(y_label)
        ax.xaxis.grid(True, alpha=0.2)
        if percentage_axis:
            ax.set_xlim(0, 100)
    else:
        ax.set_xticks(positions, categories)
        if x_label:
            ax.set_xlabel(x_label)
        ax.set_ylabel(y_label or "Value")
        ax.yaxis.grid(True, alpha=0.2)
        if percentage_axis:
            ax.set_ylim(0, 100)

    _place_legend_above(ax, labels)
    fig.tight_layout()
    return ax


def plot_stacked_percent(
    data: pd.DataFrame,
    *,
    category_col: str,
    value_cols: Sequence[str],
    series_labels: Sequence[str] | None = None,
    horizontal: bool = False,
    annotate: bool = True,
    min_label_percent: float = 7.5,
):
    """Plot a 100 percent stacked bar chart from percentage-point values."""
    frame = data[[category_col, *value_cols]].copy()
    labels = list(series_labels or value_cols)
    if len(labels) != len(value_cols):
        raise ValueError("series_labels must match value_cols")

    fig, ax = plt.subplots()
    positions = list(range(len(frame)))
    cumulative = [0.0] * len(frame)

    for column, label in zip(value_cols, labels, strict=True):
        values = frame[column].astype(float).tolist()
        if horizontal:
            bars = ax.barh(positions, values, left=cumulative, label=label)
        else:
            bars = ax.bar(positions, values, bottom=cumulative, label=label)

        if annotate:
            for bar, value, start in zip(bars, values, cumulative, strict=True):
                if value < min_label_percent:
                    continue
                label_text = f"{value:.1f}%" if value < 10 else f"{value:.0f}%"
                if horizontal:
                    ax.text(
                        start + value / 2,
                        bar.get_y() + bar.get_height() / 2,
                        label_text,
                        ha="center",
                        va="center",
                        fontsize=8,
                    )
                else:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        start + value / 2,
                        label_text,
                        ha="center",
                        va="center",
                        fontsize=8,
                    )
        cumulative = [
            previous + value
            for previous, value in zip(cumulative, values, strict=True)
        ]

    categories = frame[category_col].astype(str).tolist()
    if horizontal:
        ax.set_yticks(positions, categories)
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        ax.set_xlabel("Share of classifiable configurations (%)")
        ax.xaxis.grid(True, alpha=0.2)
    else:
        ax.set_xticks(positions, categories)
        ax.set_ylim(0, 100)
        ax.set_ylabel("Share of classifiable configurations (%)")
        ax.yaxis.grid(True, alpha=0.2)

    _place_legend_above(ax, labels)
    fig.tight_layout()
    return ax

def _pie_autopct(min_label_percent: float):
    def formatter(percent: float) -> str:
        if percent < min_label_percent:
            return ""
        return f"{percent:.1f}%"

    return formatter


def plot_pie(
    data: pd.DataFrame,
    *,
    category_col: str,
    value_col: str,
    title: str | None = None,
    legend_title: str | None = None,
    min_label_percent: float = 3.0,
):
    """Plot a single pie chart with percentages on sufficiently large slices."""
    frame = data[[category_col, value_col]].copy()
    frame[value_col] = frame[value_col].astype(float)
    frame = frame[frame[value_col] > 0]
    if frame.empty:
        raise ValueError("plot_pie requires at least one positive value")

    fig, ax = plt.subplots()
    wedges, _, _ = ax.pie(
        frame[value_col],
        startangle=90,
        counterclock=False,
        autopct=_pie_autopct(min_label_percent),
        textprops={"fontsize": 8},
    )
    ax.legend(
        wedges,
        frame[category_col].astype(str).tolist(),
        title=legend_title,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
    )
    if title:
        ax.set_title(title)
    ax.axis("equal")
    fig.tight_layout()
    return ax


def plot_pie_comparison(
    data: pd.DataFrame,
    *,
    category_col: str,
    value_cols: Sequence[str],
    series_labels: Sequence[str] | None = None,
    min_label_percent: float = 3.0,
):
    """Plot one pie per row using a shared legend for the value columns."""
    frame = data[[category_col, *value_cols]].copy()
    if frame.empty:
        raise ValueError("plot_pie_comparison requires at least one row")

    labels = list(series_labels or value_cols)
    if len(labels) != len(value_cols):
        raise ValueError("series_labels must match value_cols")

    fig, axes = plt.subplots(1, len(frame), squeeze=False)
    axes = axes[0]
    legend_wedges = None

    for ax, (_, row) in zip(axes, frame.iterrows(), strict=True):
        values = [float(row[column]) for column in value_cols]
        wedges, _, _ = ax.pie(
            values,
            startangle=90,
            counterclock=False,
            autopct=_pie_autopct(min_label_percent),
            textprops={"fontsize": 8},
        )
        legend_wedges = wedges
        ax.set_title(str(row[category_col]))
        ax.axis("equal")

    if legend_wedges is not None:
        fig.legend(
            legend_wedges,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.0),
            ncols=min(len(labels), 4),
            frameon=False,
        )

    fig.tight_layout(rect=(0, 0, 1, 0.9))
    return axes

