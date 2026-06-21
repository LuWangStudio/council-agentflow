#!/usr/bin/env python3
from __future__ import annotations

import argparse
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

from count_rerun_rate_from_agentflow_artifacts import parse_since, summarize


COUNT_METRICS = ("decisions", "rerun")
COUNT_LABELS = {
    "decisions": "Decisions",
    "rerun": "Rerun",
}
DONE_METRICS = ("done", "post_done_human_resume")
DONE_LABELS = {
    "done": "Done",
    "post_done_human_resume": "Done Human Resume",
}
DEFAULT_RUNS_DIR = Path(".agentflow-temp") / "runs"
DEFAULT_OUTPUT_DIR = Path("temp-dir")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot daily Council-Agentflow decision and rerun rates."
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Agentflow runs directory. Defaults to .agentflow-temp/runs.",
    )
    parser.add_argument(
        "--since",
        help="Filter runs since YYYY-MM-DD or '<N> days'.",
    )
    return parser.parse_args()


def output_path(prefix: str) -> Path:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"{prefix}-{timestamp}.png"


def integer_formatter(value: float, _position: int) -> str:
    return f"{value:,.0f}"


def percent_formatter(value: float, _position: int) -> str:
    return f"{value:.0%}"


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


def since_to_timestamp(since_day: object) -> pd.Timestamp | None:
    if since_day is None:
        return None
    return pd.Timestamp(since_day)


def daily_rerun_dataframe(
    summary: dict[str, Any], *, since_day: object | None
) -> pd.DataFrame:
    by_day = summary.get("by_day")
    if not isinstance(by_day, dict) or not by_day:
        return pd.DataFrame(
            columns=[
                "date",
                "decisions",
                "rerun",
                "decision_rerun_rate",
                "complexity",
                "done",
                "post_done_human_resume",
                "post_done_human_resume_rate",
            ]
        )

    rows = []
    for day, day_summary in sorted(by_day.items()):
        if not isinstance(day_summary, dict):
            continue
        parsed_day = pd.to_datetime(day, errors="coerce")
        if pd.isna(parsed_day):
            continue
        decisions = int(day_summary.get("decisions", 0))
        rerun = int(day_summary.get("rerun_execution", 0))
        done = int(day_summary.get("done", 0))
        post_done_human_resume = int(day_summary.get("post_done_human_resume", 0))
        complexity = day_summary.get("complexity")
        rows.append(
            {
                "date": parsed_day,
                "decisions": decisions,
                "rerun": rerun,
                "decision_rerun_rate": rerun / decisions if decisions else pd.NA,
                "complexity": float(complexity) if complexity is not None else pd.NA,
                "done": done,
                "post_done_human_resume": post_done_human_resume,
                "post_done_human_resume_rate": (
                    post_done_human_resume / done if done else pd.NA
                ),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "decisions",
                "rerun",
                "decision_rerun_rate",
                "complexity",
                "done",
                "post_done_human_resume",
                "post_done_human_resume_rate",
            ]
        )

    wide = pd.DataFrame(rows).set_index("date").sort_index()
    start_date = since_to_timestamp(since_day) or wide.index.min()
    end_date = wide.index.max()
    wide = wide.reindex(pd.date_range(start=start_date, end=end_date, freq="D"))
    wide[["decisions", "rerun", "done", "post_done_human_resume"]] = wide[
        ["decisions", "rerun", "done", "post_done_human_resume"]
    ].fillna(0)
    wide["decision_rerun_rate"] = wide["decision_rerun_rate"].astype("Float64")
    wide["complexity"] = wide["complexity"].astype("Float64")
    wide["post_done_human_resume_rate"] = wide[
        "post_done_human_resume_rate"
    ].astype("Float64")
    wide.index.name = "date"
    return wide.reset_index()


def count_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame(columns=["date", "metric", "count"])
    counts = data.melt(
        id_vars="date",
        value_vars=list(COUNT_METRICS),
        var_name="metric",
        value_name="count",
    )
    counts["metric"] = counts["metric"].map(COUNT_LABELS)
    counts["metric"] = pd.Categorical(
        counts["metric"],
        categories=[COUNT_LABELS[metric] for metric in COUNT_METRICS],
        ordered=True,
    )
    return counts


def done_count_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame(columns=["date", "metric", "count"])
    counts = data.melt(
        id_vars="date",
        value_vars=list(DONE_METRICS),
        var_name="metric",
        value_name="count",
    )
    counts["metric"] = counts["metric"].map(DONE_LABELS)
    counts["metric"] = pd.Categorical(
        counts["metric"],
        categories=[DONE_LABELS[metric] for metric in DONE_METRICS],
        ordered=True,
    )
    return counts


def add_count_totals(axis: plt.Axes, summary: dict[str, Any]) -> None:
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    lines = [
        "Totals",
        f"Decisions: {int(totals.get('decisions', 0)):,.0f}",
        f"Rerun: {int(totals.get('rerun_execution', 0)):,.0f}",
    ]
    add_text_box(axis, lines)


def add_rate_summary(axis: plt.Axes, summary: dict[str, Any]) -> None:
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    rate = float(totals.get("decision_rerun_rate", 0))
    complexity = totals.get("complexity")
    complexity_text = "n/a" if complexity is None else f"{float(complexity):.1f}"
    lines = [
        "Overall",
        f"Decision rerun rate: {rate:.1%}",
        f"Avg complexity: {complexity_text}",
    ]
    add_text_box(axis, lines)


def add_done_count_totals(axis: plt.Axes, summary: dict[str, Any]) -> None:
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    lines = [
        "Totals",
        f"Done: {int(totals.get('done', 0)):,.0f}",
        (
            "Done Human Resume: "
            f"{int(totals.get('post_done_human_resume', 0)):,.0f}"
        ),
    ]
    add_text_box(axis, lines)


def add_done_human_resume_rate_summary(
    axis: plt.Axes, summary: dict[str, Any]
) -> None:
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    rate = float(totals.get("post_done_human_resume_rate", 0))
    lines = ["Overall", f"Done human resume rate: {rate:.1%}"]
    add_text_box(axis, lines)


def add_combined_legend(axis: plt.Axes, handles: list[Any], labels: list[str]) -> None:
    if handles:
        axis.legend(handles, labels, title="Metric")


def add_text_box(axis: plt.Axes, lines: list[str]) -> None:
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


def plot_rerun_metrics(
    data: pd.DataFrame, *, summary: dict[str, Any], destination: Path
) -> None:
    sns.set_theme(style="whitegrid")
    dates = data["date"].drop_duplicates().sort_values().to_list()
    figure, axes = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(figure_width(len(dates)), 11),
        sharex=True,
    )
    figure.suptitle("Daily Rerun Decisions", fontsize=16)

    counts = count_dataframe(data)
    sns.lineplot(
        data=counts,
        x="date",
        y="count",
        hue="metric",
        hue_order=[COUNT_LABELS[metric] for metric in COUNT_METRICS],
        marker="o",
        linewidth=2,
        ax=axes[0],
    )
    axes[0].set_title("Decisions / Rerun Counts")
    axes[0].set_xlabel("Date")
    axes[0].set_ylabel("Count")
    axes[0].yaxis.set_major_formatter(FuncFormatter(integer_formatter))
    axes[0].legend(title="Metric")
    add_count_totals(axes[0], summary)
    configure_date_axis(axes[0], dates)

    rate_data = data.dropna(subset=["decision_rerun_rate"])
    complexity_data = data.dropna(subset=["complexity"])
    complexity_axis = axes[1].twinx()
    legend_handles: list[Any] = []
    legend_labels: list[str] = []
    if not rate_data.empty:
        sns.lineplot(
            data=rate_data,
            x="date",
            y="decision_rerun_rate",
            marker="o",
            linewidth=2,
            color="#1f77b4",
            legend=False,
            ax=axes[1],
        )
        legend_handles.append(axes[1].lines[-1])
        legend_labels.append("Decision Rerun Rate")
    if not complexity_data.empty:
        sns.lineplot(
            data=complexity_data,
            x="date",
            y="complexity",
            marker="o",
            linewidth=2,
            color="#ff7f0e",
            legend=False,
            ax=complexity_axis,
        )
        legend_handles.append(complexity_axis.lines[-1])
        legend_labels.append("Complexity")
    if rate_data.empty and complexity_data.empty:
        axes[1].text(
            0.5,
            0.5,
            "No review decision or complexity days",
            transform=axes[1].transAxes,
            va="center",
            ha="center",
        )
    add_combined_legend(axes[1], legend_handles, legend_labels)
    axes[1].set_title("Decision Rerun Rate / Complexity")
    axes[1].set_xlabel("Date")
    axes[1].set_ylabel("Decision Rerun Rate", color="#1f77b4")
    axes[1].tick_params(axis="y", labelcolor="#1f77b4")
    axes[1].yaxis.set_major_formatter(FuncFormatter(percent_formatter))
    axes[1].set_ylim(0, 1)
    complexity_axis.set_ylabel("Complexity", color="#ff7f0e")
    complexity_axis.tick_params(axis="y", labelcolor="#ff7f0e")
    complexity_axis.set_ylim(0, 100)
    add_rate_summary(axes[1], summary)
    configure_date_axis(axes[1], dates)

    figure.tight_layout(rect=(0, 0, 1, 0.96))
    figure.savefig(destination, dpi=200)
    plt.close(figure)


def plot_done_human_resume_metrics(
    data: pd.DataFrame, *, summary: dict[str, Any], destination: Path
) -> None:
    sns.set_theme(style="whitegrid")
    dates = data["date"].drop_duplicates().sort_values().to_list()
    figure, axes = plt.subplots(
        nrows=2,
        ncols=1,
        figsize=(figure_width(len(dates)), 11),
        sharex=True,
    )
    figure.suptitle("Daily Done Human Resume", fontsize=16)

    counts = done_count_dataframe(data)
    sns.lineplot(
        data=counts,
        x="date",
        y="count",
        hue="metric",
        hue_order=[DONE_LABELS[metric] for metric in DONE_METRICS],
        marker="o",
        linewidth=2,
        ax=axes[0],
    )
    axes[0].set_title("Done / Done Human Resume Counts")
    axes[0].set_xlabel("Date")
    axes[0].set_ylabel("Count")
    axes[0].yaxis.set_major_formatter(FuncFormatter(integer_formatter))
    axes[0].legend(title="Metric")
    add_done_count_totals(axes[0], summary)
    configure_date_axis(axes[0], dates)

    rate_data = data.dropna(subset=["post_done_human_resume_rate"])
    if not rate_data.empty:
        sns.lineplot(
            data=rate_data,
            x="date",
            y="post_done_human_resume_rate",
            marker="o",
            linewidth=2,
            label="Done Human Resume Rate",
            ax=axes[1],
        )
        axes[1].legend(title="Metric")
    else:
        axes[1].text(
            0.5,
            0.5,
            "No done days",
            transform=axes[1].transAxes,
            va="center",
            ha="center",
        )
    axes[1].set_title("Done Human Resume Rate")
    axes[1].set_xlabel("Date")
    axes[1].set_ylabel("Rate")
    axes[1].yaxis.set_major_formatter(FuncFormatter(percent_formatter))
    axes[1].set_ylim(0, 1)
    add_done_human_resume_rate_summary(axes[1], summary)
    configure_date_axis(axes[1], dates)

    figure.tight_layout(rect=(0, 0, 1, 0.96))
    figure.savefig(destination, dpi=200)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    runs_dir = args.runs_dir.expanduser()
    if not runs_dir.is_dir():
        print(f"Agentflow runs directory does not exist: {runs_dir}", file=sys.stderr)
        return 1

    since_day = None
    if args.since is not None:
        try:
            since_day = parse_since(args.since)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    summary = summarize(runs_dir, since_day=since_day)
    data = daily_rerun_dataframe(summary, since_day=since_day)
    if data.empty:
        print(f"No rerun decision data found under: {runs_dir}", file=sys.stderr)
        return 1

    rerun_destination = output_path("daily-rerun-rate")
    done_human_resume_destination = output_path("daily-done-human-resume")
    plot_rerun_metrics(data, summary=summary, destination=rerun_destination)
    plot_done_human_resume_metrics(
        data,
        summary=summary,
        destination=done_human_resume_destination,
    )
    print(rerun_destination)
    print(done_human_resume_destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
