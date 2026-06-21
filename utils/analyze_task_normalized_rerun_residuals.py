#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_OUTPUT_DIR = Path("temp-dir")
DEFAULT_ALPHA = 5.0
DEFAULT_BUCKET_SIZE = 20
DEFAULT_VARIANCE_FLOOR = 0.25
REQUIRED_COLUMNS = {
    "day",
    "run_id",
    "topic",
    "feature_complexity_hint",
    "outcome_rerun",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze experimental task-normalized rerun residuals from an "
            "Agentflow per-job metrics CSV."
        )
    )
    parser.add_argument(
        "--metrics-csv",
        type=Path,
        required=True,
        help="CSV produced by utils/export_agentflow_job_metrics.py.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the residual CSV. Defaults to temp-dir.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_ALPHA,
        help=(
            "Smoothing strength. Higher values shrink bucket expectations more "
            "toward the global mean. Defaults to 5.0."
        ),
    )
    parser.add_argument(
        "--bucket-size",
        type=int,
        default=DEFAULT_BUCKET_SIZE,
        help="Complexity bucket size over 1-100. Defaults to 20.",
    )
    parser.add_argument(
        "--variance-floor",
        type=float,
        default=DEFAULT_VARIANCE_FLOOR,
        help=(
            "Minimum expected count used in standardized residual denominator. "
            "Defaults to 0.25."
        ),
    )
    return parser.parse_args()


def safe_filename_part(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return sanitized or "metrics"


def output_path(metrics_csv: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    stem = safe_filename_part(metrics_csv.stem)
    return output_dir / f"task-normalized-rerun-residuals-{stem}-{timestamp}.csv"


def complexity_bucket(value: float, *, bucket_size: int) -> str:
    clamped = min(max(float(value), 1.0), 100.0)
    lower = int((math.ceil(clamped) - 1) // bucket_size * bucket_size + 1)
    upper = min(lower + bucket_size - 1, 100)
    return f"{lower:03d}-{upper:03d}"


def validate_args(args: argparse.Namespace) -> str | None:
    if not args.metrics_csv.is_file():
        return f"Metrics CSV does not exist: {args.metrics_csv}"
    if args.alpha < 0:
        return "--alpha must be >= 0"
    if args.bucket_size <= 0 or args.bucket_size > 100:
        return "--bucket-size must be in the range 1..100"
    if args.variance_floor <= 0:
        return "--variance-floor must be > 0"
    return None


def load_metrics(path: Path, *, bucket_size: int) -> pd.DataFrame:
    data = pd.read_csv(path)
    missing = sorted(REQUIRED_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(f"metrics CSV is missing required columns: {', '.join(missing)}")

    data = data.copy()
    data["feature_complexity_hint"] = pd.to_numeric(
        data["feature_complexity_hint"],
        errors="coerce",
    )
    data["outcome_rerun"] = pd.to_numeric(data["outcome_rerun"], errors="coerce")
    data = data.dropna(subset=["feature_complexity_hint", "outcome_rerun"])
    data = data[
        data["feature_complexity_hint"].between(1, 100)
        & (data["outcome_rerun"] >= 0)
    ].copy()
    if data.empty:
        raise ValueError("metrics CSV has no rows with valid complexity and rerun values")

    data["complexity_bucket"] = data["feature_complexity_hint"].apply(
        lambda value: complexity_bucket(float(value), bucket_size=bucket_size)
    )
    return data


def leave_one_out_global_mean(total_reruns: float, row_reruns: float, row_count: int) -> float:
    if row_count <= 1:
        return total_reruns / row_count if row_count else 0.0
    return (total_reruns - row_reruns) / (row_count - 1)


def calculate_residuals(
    data: pd.DataFrame, *, alpha: float, variance_floor: float
) -> pd.DataFrame:
    group_columns = ["complexity_bucket"]
    row_count = len(data)
    total_reruns = float(data["outcome_rerun"].sum())
    global_mean = total_reruns / row_count if row_count else 0.0

    grouped = data.groupby(group_columns, dropna=False)["outcome_rerun"].agg(
        bucket_job_count="count",
        bucket_rerun_sum="sum",
        bucket_raw_mean="mean",
    )
    result = data.join(grouped, on=group_columns)

    expected_values: list[float] = []
    global_loo_values: list[float] = []
    for row in result.itertuples(index=False):
        observed = float(row.outcome_rerun)
        bucket_sum_excluding_self = float(row.bucket_rerun_sum) - observed
        bucket_count_excluding_self = int(row.bucket_job_count) - 1
        global_mean_loo = leave_one_out_global_mean(total_reruns, observed, row_count)
        expected = (
            bucket_sum_excluding_self + alpha * global_mean_loo
        ) / (bucket_count_excluding_self + alpha)
        expected_values.append(expected)
        global_loo_values.append(global_mean_loo)

    result["baseline_global_mean_reruns"] = global_mean
    result["baseline_global_mean_reruns_loo"] = global_loo_values
    result["baseline_bucket_job_count"] = result["bucket_job_count"].astype(int)
    result["baseline_bucket_raw_mean_reruns"] = result["bucket_raw_mean"].astype(float)
    result["expected_reruns"] = expected_values
    result["rerun_residual"] = result["outcome_rerun"] - result["expected_reruns"]
    result["standardized_rerun_residual"] = result.apply(
        lambda row: row["rerun_residual"]
        / math.sqrt(max(float(row["expected_reruns"]), variance_floor)),
        axis=1,
    )
    result["relative_rerun_residual"] = result.apply(
        lambda row: (
            row["rerun_residual"] / row["expected_reruns"]
            if row["expected_reruns"] > 0
            else float("nan")
        ),
        axis=1,
    )
    result["residual_baseline"] = "leave_one_out_smoothed_mean"
    result["residual_grouping"] = "complexity_bucket"
    result["residual_alpha"] = alpha
    result = result.drop(columns=["bucket_job_count", "bucket_rerun_sum", "bucket_raw_mean"])
    return result.sort_values(
        by=["standardized_rerun_residual", "rerun_residual", "outcome_rerun"],
        ascending=[False, False, False],
    )


def output_columns(data: pd.DataFrame) -> list[str]:
    preferred = [
        "day",
        "run_id",
        "topic",
        "feature_complexity_hint",
        "complexity_bucket",
        "outcome_rerun",
        "expected_reruns",
        "rerun_residual",
        "standardized_rerun_residual",
        "relative_rerun_residual",
        "baseline_bucket_job_count",
        "baseline_bucket_raw_mean_reruns",
        "baseline_global_mean_reruns",
        "baseline_global_mean_reruns_loo",
        "residual_baseline",
        "residual_grouping",
        "residual_alpha",
        "outcome_execution_rounds",
        "outcome_decisions",
        "outcome_human_review",
        "outcome_done",
        "outcome_decision_rerun_rate",
        "outcome_terminal_action",
        "feature_execution_model",
        "feature_execution_variant",
        "feature_review_decision_model",
        "feature_review_decision_variant",
        "metadata_path",
        "feature_task",
    ]
    return [column for column in preferred if column in data.columns]


def print_top_residuals(data: pd.DataFrame, *, limit: int = 10) -> None:
    print("\nTop positive standardized residuals:")
    columns = [
        "standardized_rerun_residual",
        "rerun_residual",
        "outcome_rerun",
        "expected_reruns",
        "feature_complexity_hint",
        "topic",
    ]
    for row in data.head(limit)[columns].itertuples(index=False):
        print(
            f"  std={row.standardized_rerun_residual:.2f} "
            f"residual={row.rerun_residual:.2f} "
            f"observed={row.outcome_rerun:.0f} "
            f"expected={row.expected_reruns:.2f} "
            f"complexity={row.feature_complexity_hint:g} "
            f"topic={row.topic}"
        )


def main() -> int:
    args = parse_args()
    validation_error = validate_args(args)
    if validation_error is not None:
        print(validation_error, file=sys.stderr)
        return 1

    try:
        metrics = load_metrics(args.metrics_csv, bucket_size=args.bucket_size)
        residuals = calculate_residuals(
            metrics,
            alpha=args.alpha,
            variance_floor=args.variance_floor,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    destination = output_path(args.metrics_csv, args.output_dir.expanduser())
    residuals.to_csv(destination, columns=output_columns(residuals), index=False)
    print(destination)
    print(f"rows: {len(residuals)}")
    print("experimental: true")
    print_top_residuals(residuals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
