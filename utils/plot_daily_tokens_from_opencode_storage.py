#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.ticker import FuncFormatter

from count_daily_tokens_from_opencode_storage import (
    display_workspace,
    parse_since,
    summarize,
)


PLOT_METRICS = ("input", "cache_read", "output", "reasoning")
PLOT_GROUPS = (
    ("Input / Cache Read Tokens", ("input", "cache_read")),
    ("Output / Reasoning Tokens", ("output", "reasoning")),
)
METRIC_LABELS = {
    "input": "Input",
    "cache_read": "Cache Read",
    "output": "Output",
    "reasoning": "Reasoning",
}
DEFAULT_DATABASE = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
DEFAULT_OUTPUT_DIR = Path("temp-dir")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot daily token usage for one OpenCode workspace."
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace path to plot. Only this workspace is included.",
    )
    parser.add_argument(
        "--since",
        help="Filter messages since YYYY-MM-DD or '<N> days'.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help="OpenCode SQLite database path. Defaults to ~/.local/share/opencode/opencode.db.",
    )
    return parser.parse_args()


def safe_filename_part(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return sanitized or "workspace"


def output_path(workspace: str) -> Path:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    workspace_name = safe_filename_part(display_workspace(workspace))
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"daily-tokens-{workspace_name}-{timestamp}.png"


def token_formatter(value: float, _position: int) -> str:
    return f"{value:,.0f}"


def since_day(since_timestamp: float | None) -> pd.Timestamp | None:
    if since_timestamp is None:
        return None
    seconds = since_timestamp / 1000 if since_timestamp > 10_000_000_000 else since_timestamp
    return pd.Timestamp(datetime.fromtimestamp(seconds).date())


def daily_tokens_dataframe(
    summary: dict[str, Any], *, since_timestamp: float | None
) -> pd.DataFrame:
    by_day = summary.get("by_day")
    if not isinstance(by_day, dict) or not by_day:
        return pd.DataFrame(columns=["date", "metric", "tokens"])

    rows = []
    for day, day_summary in sorted(by_day.items()):
        if not isinstance(day_summary, dict):
            continue
        date = pd.to_datetime(day, errors="coerce")
        if pd.isna(date):
            continue
        row = {"date": date}
        row.update({metric: float(day_summary.get(metric, 0)) for metric in PLOT_METRICS})
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["date", "metric", "tokens"])

    wide = pd.DataFrame(rows).set_index("date").sort_index()
    start_date = since_day(since_timestamp) or wide.index.min()
    end_date = wide.index.max()
    wide = wide.reindex(
        pd.date_range(start=start_date, end=end_date, freq="D"),
        fill_value=0,
    )
    wide.index.name = "date"
    data = wide.reset_index().melt(
        id_vars="date",
        value_vars=list(PLOT_METRICS),
        var_name="metric",
        value_name="tokens",
    )
    data["metric"] = data["metric"].map(METRIC_LABELS)
    data["metric"] = pd.Categorical(
        data["metric"],
        categories=[METRIC_LABELS[metric] for metric in PLOT_METRICS],
        ordered=True,
    )
    return data


def figure_width(day_count: int) -> float:
    return min(max(14.0, day_count * 0.22), 36.0)


def configure_date_axis(axis: plt.Axes, dates: list[pd.Timestamp]) -> None:
    axis.set_xticks(dates)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    axis.tick_params(axis="x", labelbottom=True, labelrotation=90, labelsize=7)
    if len(dates) == 1:
        axis.set_xlim(
            dates[0] - pd.Timedelta(hours=12),
            dates[0] + pd.Timedelta(hours=12),
        )
    else:
        axis.set_xlim(dates[0], dates[-1])


def add_metric_totals(axis: plt.Axes, data: pd.DataFrame, labels: list[str]) -> None:
    lines = ["Totals"]
    for label in labels:
        total = data.loc[data["metric"] == label, "tokens"].sum()
        lines.append(f"{label}: {total:,.0f}")

    axis.text(
        0.01,
        0.97,
        "\n".join(lines),
        transform=axis.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "#cccccc",
            "alpha": 0.85,
        },
    )


def plot_daily_tokens(data: pd.DataFrame, *, workspace: str, destination: Path) -> None:
    sns.set_theme(style="whitegrid")
    dates = data["date"].drop_duplicates().sort_values().to_list()
    figure, axes = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(figure_width(len(dates)), 11),
        sharex=True,
    )
    figure.suptitle("Daily Token Usage", fontsize=16)

    for axis, (title, metrics) in zip(axes, PLOT_GROUPS, strict=True):
        labels = [METRIC_LABELS[metric] for metric in metrics]
        chart_data = data[data["metric"].isin(labels)]
        sns.lineplot(
            data=chart_data,
            x="date",
            y="tokens",
            hue="metric",
            hue_order=labels,
            marker="o",
            linewidth=2,
            ax=axis,
        )

        axis.set_title(title)
        axis.set_xlabel("Date")
        axis.set_ylabel("Tokens")
        axis.yaxis.set_major_formatter(FuncFormatter(token_formatter))
        axis.legend(title="Metric")
        add_metric_totals(axis, chart_data, labels)
        configure_date_axis(axis, dates)

    figure.tight_layout(rect=(0, 0, 1, 0.96))
    figure.savefig(destination, dpi=200)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    if not args.database.is_file():
        print(f"OpenCode SQLite database does not exist: {args.database}", file=sys.stderr)
        return 1

    since_timestamp = None
    if args.since is not None:
        try:
            since_timestamp = parse_since(args.since)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    summary = summarize(
        args.database,
        workspace=args.workspace,
        since_timestamp=since_timestamp,
    )
    data = daily_tokens_dataframe(summary, since_timestamp=since_timestamp)
    if data.empty:
        print(f"No daily token data found for workspace: {args.workspace}", file=sys.stderr)
        return 1

    workspace_filter = str(summary.get("workspace_filter") or args.workspace)
    destination = output_path(workspace_filter)
    plot_daily_tokens(
        data,
        workspace=display_workspace(workspace_filter),
        destination=destination,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
