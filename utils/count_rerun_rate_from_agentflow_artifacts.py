#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


REVIEW_DECISION_ACTIONS = ("rerun_execution", "human_review", "done")
LOOP_DETECTOR_ACTIONS = ("continue", "human_review")
LEFT_ALIGN_KEYS = {"date"}
JOB_METADATA_FILENAME = "job-metadata.json"


@dataclass
class DecisionRecord:
    day: str
    run_id: str
    job: str
    path: Path
    next_action: str
    reason: str
    cycle_number: int | None
    iteration_number: int | None


@dataclass
class LoopRecord:
    day: str
    run_id: str
    job: str
    path: Path
    next_action: str
    reason: str
    cycle_number: int | None
    iteration_number: int | None


@dataclass
class ExecutionRecord:
    day: str
    run_id: str
    job: str
    path: Path
    cycle_number: int | None
    iteration_number: int | None


@dataclass
class JobMetadataRecord:
    day: str
    run_id: str
    job: str
    path: Path
    complexity_hint: float


@dataclass
class DayStats:
    runs: set[str] = field(default_factory=set)
    jobs: set[str] = field(default_factory=set)
    actions: Counter[str] = field(default_factory=Counter)
    loop_actions: Counter[str] = field(default_factory=Counter)
    job_execution_rounds: Counter[str] = field(default_factory=Counter)
    job_complexities: dict[str, float] = field(default_factory=dict)
    decisions: int = 0
    loop_detector_invocations: int = 0
    execution_rounds: int = 0

    def add(self, record: DecisionRecord) -> None:
        self.runs.add(record.run_id)
        self.jobs.add(f"{record.run_id}/{record.job}")
        self.actions[record.next_action] += 1
        self.decisions += 1

    def add_loop(self, record: LoopRecord) -> None:
        self.runs.add(record.run_id)
        self.jobs.add(f"{record.run_id}/{record.job}")
        self.loop_actions[record.next_action] += 1
        self.loop_detector_invocations += 1

    def add_execution(self, record: ExecutionRecord) -> None:
        job_key = f"{record.run_id}/{record.job}"
        self.runs.add(record.run_id)
        self.jobs.add(job_key)
        self.job_execution_rounds[job_key] += 1
        self.execution_rounds += 1

    def add_job_metadata(self, record: JobMetadataRecord) -> None:
        job_key = f"{record.run_id}/{record.job}"
        self.job_complexities[job_key] = record.complexity_hint


def parse_since(value: str) -> date:
    stripped = value.strip()
    try:
        return datetime.strptime(stripped, "%Y-%m-%d").date()
    except ValueError:
        pass

    parts = stripped.split()
    if len(parts) == 2 and parts[1] == "days" and parts[0].isdigit():
        return datetime.now().astimezone().date() - timedelta(days=int(parts[0]))

    raise ValueError("invalid --since")


def run_id_to_day(run_id: str) -> str | None:
    if len(run_id) < 8 or not run_id[:8].isdigit():
        return None
    try:
        return datetime.strptime(run_id[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def path_mtime_day(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime(
        "%Y-%m-%d"
    )


def parse_cycle_iteration(path: Path) -> tuple[int | None, int | None]:
    stem = path.name
    cycle_marker = "cycle-"
    iteration_marker = "iteration-"
    cycle_number = None
    iteration_number = None

    if cycle_marker in stem:
        cycle_part = stem.split(cycle_marker, 1)[1].split("-", 1)[0]
        if cycle_part.isdigit():
            cycle_number = int(cycle_part)
    if iteration_marker in stem:
        iteration_part = stem.split(iteration_marker, 1)[1].split(".", 1)[0]
        if iteration_part.isdigit():
            iteration_number = int(iteration_part)

    return cycle_number, iteration_number


def is_review_decision_artifact(path: Path) -> bool:
    name = path.name.lower()
    if not path.is_file():
        return False
    if name.endswith(".review.txt"):
        return False
    return "review-decision" in name or "review_decision" in name


def is_loop_detector_artifact(path: Path) -> bool:
    name = path.name.lower()
    if not path.is_file():
        return False
    return "loop-detector" in name or "loop_detector" in name


def is_execution_artifact(path: Path) -> bool:
    name = path.name.lower()
    if not path.is_file():
        return False
    if not name.endswith(".txt"):
        return False
    if "cycle-" not in name or "iteration-" not in name:
        return False
    return name.startswith("execution") or "execution-cycle-" in name


def is_job_metadata_artifact(path: Path) -> bool:
    return path.is_file() and path.name == JOB_METADATA_FILENAME


def parse_complexity_hint(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
    else:
        return None

    if not 1 <= parsed <= 100:
        return None
    return parsed


def load_review_decision(path: Path) -> tuple[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    next_action = payload.get("next_action")
    if not isinstance(next_action, str) or next_action not in REVIEW_DECISION_ACTIONS:
        return None

    reason = payload.get("reason")
    return next_action, reason if isinstance(reason, str) else ""


def load_loop_detector(path: Path) -> tuple[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    next_action = payload.get("next_action")
    if not isinstance(next_action, str) or next_action not in LOOP_DETECTOR_ACTIONS:
        return None

    reason = payload.get("reason")
    return next_action, reason if isinstance(reason, str) else ""


def load_job_metadata_complexity(path: Path) -> float | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return parse_complexity_hint(metadata.get("complexity_hint"))


def record_from_artifact(runs_dir: Path, path: Path) -> DecisionRecord | None:
    decision = load_review_decision(path)
    if decision is None:
        return None

    try:
        relative_parts = path.relative_to(runs_dir).parts
    except ValueError:
        relative_parts = path.parts

    run_id = relative_parts[0] if len(relative_parts) >= 1 else "unknown"
    job = relative_parts[1] if len(relative_parts) >= 2 else path.parent.name
    day = run_id_to_day(run_id) or path_mtime_day(path)
    cycle_number, iteration_number = parse_cycle_iteration(path)
    next_action, reason = decision

    return DecisionRecord(
        day=day,
        run_id=run_id,
        job=job,
        path=path,
        next_action=next_action,
        reason=reason,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
    )


def loop_record_from_artifact(runs_dir: Path, path: Path) -> LoopRecord | None:
    decision = load_loop_detector(path)
    if decision is None:
        return None

    try:
        relative_parts = path.relative_to(runs_dir).parts
    except ValueError:
        relative_parts = path.parts

    run_id = relative_parts[0] if len(relative_parts) >= 1 else "unknown"
    job = relative_parts[1] if len(relative_parts) >= 2 else path.parent.name
    day = run_id_to_day(run_id) or path_mtime_day(path)
    cycle_number, iteration_number = parse_cycle_iteration(path)
    next_action, reason = decision

    return LoopRecord(
        day=day,
        run_id=run_id,
        job=job,
        path=path,
        next_action=next_action,
        reason=reason,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
    )


def execution_record_from_artifact(
    runs_dir: Path, path: Path
) -> ExecutionRecord | None:
    try:
        relative_parts = path.relative_to(runs_dir).parts
    except ValueError:
        relative_parts = path.parts

    run_id = relative_parts[0] if len(relative_parts) >= 1 else "unknown"
    job = relative_parts[1] if len(relative_parts) >= 2 else path.parent.name
    day = run_id_to_day(run_id) or path_mtime_day(path)
    cycle_number, iteration_number = parse_cycle_iteration(path)

    return ExecutionRecord(
        day=day,
        run_id=run_id,
        job=job,
        path=path,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
    )


def job_metadata_record_from_artifact(
    runs_dir: Path, path: Path
) -> JobMetadataRecord | None:
    complexity_hint = load_job_metadata_complexity(path)
    if complexity_hint is None:
        return None

    try:
        relative_parts = path.relative_to(runs_dir).parts
    except ValueError:
        relative_parts = path.parts

    run_id = relative_parts[0] if len(relative_parts) >= 1 else "unknown"
    job = relative_parts[1] if len(relative_parts) >= 2 else path.parent.name
    day = run_id_to_day(run_id) or path_mtime_day(path)

    return JobMetadataRecord(
        day=day,
        run_id=run_id,
        job=job,
        path=path,
        complexity_hint=complexity_hint,
    )


def iter_review_decision_artifacts(runs_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in runs_dir.rglob("*")
        if is_review_decision_artifact(path)
    )


def iter_execution_artifacts(runs_dir: Path) -> list[Path]:
    return sorted(
        path for path in runs_dir.rglob("*") if is_execution_artifact(path)
    )


def iter_loop_detector_artifacts(runs_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in runs_dir.rglob("*")
        if is_loop_detector_artifact(path)
    )


def iter_job_metadata_artifacts(runs_dir: Path) -> list[Path]:
    return sorted(
        path for path in runs_dir.rglob("*") if is_job_metadata_artifact(path)
    )


def summarize(runs_dir: Path, *, since_day: date | None) -> dict[str, Any]:
    runs_dir = runs_dir.expanduser().resolve()
    execution_artifacts = iter_execution_artifacts(runs_dir)
    review_artifacts = iter_review_decision_artifacts(runs_dir)
    loop_artifacts = iter_loop_detector_artifacts(runs_dir)
    job_metadata_artifacts = iter_job_metadata_artifacts(runs_dir)
    ignored_review_artifacts = 0
    ignored_loop_artifacts = 0
    ignored_job_metadata_artifacts = 0
    execution_records: list[ExecutionRecord] = []
    records: list[DecisionRecord] = []
    loop_records: list[LoopRecord] = []
    job_metadata_records: list[JobMetadataRecord] = []

    for artifact in execution_artifacts:
        record = execution_record_from_artifact(runs_dir, artifact)
        if record is None:
            continue
        if since_day is not None and date.fromisoformat(record.day) < since_day:
            continue
        execution_records.append(record)

    for artifact in review_artifacts:
        record = record_from_artifact(runs_dir, artifact)
        if record is None:
            ignored_review_artifacts += 1
            continue
        if since_day is not None and date.fromisoformat(record.day) < since_day:
            continue
        records.append(record)

    for artifact in loop_artifacts:
        record = loop_record_from_artifact(runs_dir, artifact)
        if record is None:
            ignored_loop_artifacts += 1
            continue
        if since_day is not None and date.fromisoformat(record.day) < since_day:
            continue
        loop_records.append(record)

    for artifact in job_metadata_artifacts:
        record = job_metadata_record_from_artifact(runs_dir, artifact)
        if record is None:
            ignored_job_metadata_artifacts += 1
            continue
        if since_day is not None and date.fromisoformat(record.day) < since_day:
            continue
        job_metadata_records.append(record)

    execution_records.sort(
        key=lambda record: (
            record.day,
            record.run_id,
            record.job,
            record.cycle_number or 0,
            record.iteration_number or 0,
            str(record.path),
        )
    )
    records.sort(
        key=lambda record: (
            record.day,
            record.run_id,
            record.job,
            record.cycle_number or 0,
            record.iteration_number or 0,
            str(record.path),
        )
    )
    loop_records.sort(
        key=lambda record: (
            record.day,
            record.run_id,
            record.job,
            record.cycle_number or 0,
            record.iteration_number or 0,
            str(record.path),
        )
    )
    job_metadata_records.sort(
        key=lambda record: (
            record.day,
            record.run_id,
            record.job,
            str(record.path),
        )
    )

    by_day: defaultdict[str, DayStats] = defaultdict(DayStats)
    totals = DayStats()
    for record in execution_records:
        by_day[record.day].add_execution(record)
        totals.add_execution(record)
    for record in records:
        by_day[record.day].add(record)
        totals.add(record)
    for record in loop_records:
        by_day[record.day].add_loop(record)
        totals.add_loop(record)
    for record in job_metadata_records:
        by_day[record.day].add_job_metadata(record)
        totals.add_job_metadata(record)

    return {
        "runs_dir": str(runs_dir),
        "since_day": since_day.isoformat() if since_day is not None else None,
        "execution_artifacts": len(execution_artifacts),
        "execution_rounds": len(execution_records),
        "review_decision_artifacts": len(review_artifacts),
        "ignored_review_decision_artifacts": ignored_review_artifacts,
        "loop_detector_artifacts": len(loop_artifacts),
        "ignored_loop_detector_artifacts": ignored_loop_artifacts,
        "job_metadata_artifacts": len(job_metadata_artifacts),
        "ignored_job_metadata_artifacts": ignored_job_metadata_artifacts,
        "decisions": len(records),
        "loop_detector_invocations": len(loop_records),
        "by_day": {
            day: stats_to_dict(stats)
            for day, stats in sorted(by_day.items())
        },
        "totals": stats_to_dict(totals),
        "execution_records": [
            execution_record_to_dict(record, runs_dir=runs_dir)
            for record in execution_records
        ],
        "records": [record_to_dict(record, runs_dir=runs_dir) for record in records],
        "loop_records": [
            loop_record_to_dict(record, runs_dir=runs_dir)
            for record in loop_records
        ],
        "job_metadata_records": [
            job_metadata_record_to_dict(record, runs_dir=runs_dir)
            for record in job_metadata_records
        ],
    }


def stats_to_dict(stats: DayStats) -> dict[str, Any]:
    complexity = average(stats.job_complexities.values())
    return {
        "runs": len(stats.runs),
        "jobs": len(stats.jobs),
        "complexity": complexity,
        "complexity_jobs": len(stats.job_complexities),
        "execution_rounds": stats.execution_rounds,
        "rounds_per_job": safe_rate(stats.execution_rounds, len(stats.jobs)),
        "max_rounds_per_job": max(stats.job_execution_rounds.values(), default=0),
        "decisions": stats.decisions,
        **{action: stats.actions[action] for action in REVIEW_DECISION_ACTIONS},
        "decision_rerun_rate": safe_rate(
            stats.actions["rerun_execution"],
            stats.decisions,
        ),
        "loop_detect": stats.loop_detector_invocations,
        "loop_con": stats.loop_actions["continue"],
        "loop_hum": stats.loop_actions["human_review"],
        "loop_hum_rat": safe_rate(
            stats.loop_actions["human_review"],
            stats.loop_detector_invocations,
        ),
    }


def record_to_dict(record: DecisionRecord, *, runs_dir: Path) -> dict[str, Any]:
    try:
        path = str(record.path.relative_to(runs_dir))
    except ValueError:
        path = str(record.path)
    return {
        "day": record.day,
        "run_id": record.run_id,
        "job": record.job,
        "cycle_number": record.cycle_number,
        "iteration_number": record.iteration_number,
        "next_action": record.next_action,
        "reason": record.reason,
        "path": path,
    }


def loop_record_to_dict(record: LoopRecord, *, runs_dir: Path) -> dict[str, Any]:
    try:
        path = str(record.path.relative_to(runs_dir))
    except ValueError:
        path = str(record.path)
    return {
        "day": record.day,
        "run_id": record.run_id,
        "job": record.job,
        "cycle_number": record.cycle_number,
        "iteration_number": record.iteration_number,
        "next_action": record.next_action,
        "reason": record.reason,
        "path": path,
    }


def execution_record_to_dict(record: ExecutionRecord, *, runs_dir: Path) -> dict[str, Any]:
    try:
        path = str(record.path.relative_to(runs_dir))
    except ValueError:
        path = str(record.path)
    return {
        "day": record.day,
        "run_id": record.run_id,
        "job": record.job,
        "cycle_number": record.cycle_number,
        "iteration_number": record.iteration_number,
        "path": path,
    }


def job_metadata_record_to_dict(
    record: JobMetadataRecord, *, runs_dir: Path
) -> dict[str, Any]:
    try:
        path = str(record.path.relative_to(runs_dir))
    except ValueError:
        path = str(record.path)
    return {
        "day": record.day,
        "run_id": record.run_id,
        "job": record.job,
        "complexity_hint": record.complexity_hint,
        "path": path,
    }


def safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def average(values: Any) -> float | None:
    values_list = list(values)
    if not values_list:
        return None
    return sum(values_list) / len(values_list)


def format_rate(value: float) -> str:
    return f"{value:.1%}"


def format_decimal(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_optional_decimal(value: object) -> str:
    if value is None:
        return "n/a"
    return format_decimal(float(value))


def table_rows(summary: dict[str, Any], *, show_loop_info: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    for day, stats in summary["by_day"].items():
        rows.append(row_from_stats(day, stats, show_loop_info=show_loop_info))
    rows.append(
        row_from_stats("TOTAL", summary["totals"], show_loop_info=show_loop_info)
    )
    return rows


def row_from_stats(
    label: str, stats: dict[str, Any], *, show_loop_info: bool
) -> list[str]:
    row = [
        label,
        str(stats["jobs"]),
        format_optional_decimal(stats["complexity"]),
        str(stats["execution_rounds"]),
        format_decimal(float(stats["rounds_per_job"])),
        str(stats["decisions"]),
        str(stats["rerun_execution"]),
        str(stats["human_review"]),
        str(stats["done"]),
        format_rate(float(stats["decision_rerun_rate"])),
    ]
    if show_loop_info:
        row.extend(
            [
                str(stats["loop_detect"]),
                str(stats["loop_con"]),
                str(stats["loop_hum"]),
                format_rate(float(stats["loop_hum_rat"])),
            ]
        )
    return row


def print_text_summary(summary: dict[str, Any], *, show_loop_info: bool) -> None:
    print(f"runs_dir: {summary['runs_dir']}")
    if summary["since_day"] is not None:
        print(f"since_day: {summary['since_day']}")
    print(f"execution_artifacts: {summary['execution_artifacts']}")
    print(f"execution_rounds: {summary['execution_rounds']}")
    print(f"review_decision_artifacts: {summary['review_decision_artifacts']}")
    if summary["ignored_review_decision_artifacts"]:
        print(
            "ignored_review_decision_artifacts: "
            f"{summary['ignored_review_decision_artifacts']}"
        )
    print(f"loop_detector_artifacts: {summary['loop_detector_artifacts']}")
    if summary["ignored_loop_detector_artifacts"]:
        print(
            "ignored_loop_detector_artifacts: "
            f"{summary['ignored_loop_detector_artifacts']}"
        )
    print(f"job_metadata_artifacts: {summary['job_metadata_artifacts']}")
    if summary["ignored_job_metadata_artifacts"]:
        print(
            "ignored_job_metadata_artifacts: "
            f"{summary['ignored_job_metadata_artifacts']}"
        )
    print(f"decisions: {summary['decisions']}")
    print(f"loop_detector_invocations: {summary['loop_detector_invocations']}")

    headers = [
        "date",
        "jobs",
        "complexity",
        "rounds",
        "rounds/job",
        "decisions",
        "rerun",
        "human_review",
        "done",
        "decision_rerun_rate",
    ]
    if show_loop_info:
        headers.extend(
            [
                "loop_detect",
                "loop_con",
                "loop_hum",
                "loop_hum_rat",
            ]
        )
    table = [headers, *table_rows(summary, show_loop_info=show_loop_info)]
    widths = [max(len(row[index]) for row in table) for index in range(len(headers))]

    print()
    for row_index, row in enumerate(table):
        formatted_cells = []
        for index, cell in enumerate(row):
            key = headers[index]
            if key in LEFT_ALIGN_KEYS:
                formatted_cells.append(cell.ljust(widths[index]))
            else:
                formatted_cells.append(cell.rjust(widths[index]))
        print("  ".join(formatted_cells))
        if row_index == 0:
            print("  ".join("-" * width for width in widths))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize rerun rates from Council-Agentflow review decision artifacts."
        )
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path(".agentflow-temp") / "runs",
        help="Agentflow runs directory. Defaults to .agentflow-temp/runs.",
    )
    parser.add_argument(
        "--since",
        help="Filter runs since YYYY-MM-DD or '<N> days'.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON instead of text.",
    )
    parser.add_argument(
        "--show-loop-info",
        action="store_true",
        help="Show loop detector columns in text output.",
    )
    return parser.parse_args()


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
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_text_summary(summary, show_loop_info=args.show_loop_info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
