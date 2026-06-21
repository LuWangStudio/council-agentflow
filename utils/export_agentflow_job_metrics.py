#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from count_rerun_rate_from_agentflow_artifacts import (
    parse_complexity_hint,
    parse_since,
    run_id_to_day,
    summarize,
)


DEFAULT_RUNS_DIR = Path(".agentflow-temp") / "runs"
DEFAULT_OUTPUT_DIR = Path("temp-dir")
AGENT_ROLES = (
    "execution",
    "reviewer_1",
    "reviewer_2",
    "review_decision",
    "autonomy_decision",
    "loop_detector",
)
REVIEW_ACTIONS = ("rerun_execution", "human_review", "done")
LOOP_ACTIONS = ("continue", "human_review")


@dataclass
class JobMetrics:
    run_id: str
    topic: str
    day: str
    feature_metadata_available: bool = False
    feature_complexity_hint: float | None = None
    feature_task: str = ""
    agent_models: dict[str, str] = field(default_factory=dict)
    agent_variants: dict[str, str] = field(default_factory=dict)
    outcome_execution_rounds: int = 0
    review_actions: Counter[str] = field(default_factory=Counter)
    loop_actions: Counter[str] = field(default_factory=Counter)
    outcome_terminal_action: str = ""
    outcome_terminal_reason: str = ""
    outcome_terminal_cycle_number: int | None = None
    outcome_terminal_iteration_number: int | None = None
    metadata_path: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export per-job Council-Agentflow metrics as a CSV file."
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the exported CSV. Defaults to temp-dir.",
    )
    return parser.parse_args()


def safe_filename_part(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return sanitized or "agentflow"


def output_path(runs_dir: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_name = safe_filename_part(workspace_name_for_runs_dir(runs_dir))
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return output_dir / f"agentflow-job-metrics-{workspace_name}-{timestamp}.csv"


def workspace_name_for_runs_dir(runs_dir: Path) -> str:
    # Expected shape: <workspace>/<agentflow-temp-dir>/runs.
    if len(runs_dir.parents) >= 2:
        return runs_dir.parents[1].name
    return runs_dir.name


def job_key(run_id: str, topic: str) -> tuple[str, str]:
    return run_id, topic


def ensure_job(
    jobs: dict[tuple[str, str], JobMetrics], *, run_id: str, topic: str, day: str | None
) -> JobMetrics:
    key = job_key(run_id, topic)
    if key not in jobs:
        jobs[key] = JobMetrics(
            run_id=run_id,
            topic=topic,
            day=day or run_id_to_day(run_id) or "",
        )
    elif day and (not jobs[key].day or day < jobs[key].day):
        jobs[key].day = day
    return jobs[key]


def load_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def relative_or_absolute(path: Path, *, base_dir: Path) -> str:
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def metadata_path_from_record(record: dict[str, Any], *, runs_dir: Path) -> Path | None:
    value = record.get("path")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return runs_dir / path


def apply_metadata_record(
    job: JobMetrics, record: dict[str, Any], *, runs_dir: Path
) -> None:
    path = metadata_path_from_record(record, runs_dir=runs_dir)
    if path is None:
        return

    payload = load_json_mapping(path)
    if payload is None:
        return

    job.feature_metadata_available = True
    job.metadata_path = relative_or_absolute(path, base_dir=runs_dir)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        job.feature_complexity_hint = parse_complexity_hint(
            metadata.get("complexity_hint")
        )

    task = payload.get("task")
    if isinstance(task, str):
        job.feature_task = task

    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return
    for role in AGENT_ROLES:
        agent = agents.get(role)
        if not isinstance(agent, dict):
            continue
        model = agent.get("model")
        variant = agent.get("variant")
        if isinstance(model, str):
            job.agent_models[role] = model
        if isinstance(variant, str):
            job.agent_variants[role] = variant


def should_replace_terminal_action(
    job: JobMetrics, record: dict[str, Any]
) -> bool:
    current = (
        job.outcome_terminal_cycle_number or 0,
        job.outcome_terminal_iteration_number or 0,
    )
    candidate = (
        int(record.get("cycle_number") or 0),
        int(record.get("iteration_number") or 0),
    )
    return candidate >= current


def aggregate_job_metrics(summary: dict[str, Any], *, runs_dir: Path) -> list[JobMetrics]:
    jobs: dict[tuple[str, str], JobMetrics] = {}

    for record in summary.get("execution_records", []):
        if not isinstance(record, dict):
            continue
        run_id = str(record.get("run_id") or "unknown")
        topic = str(record.get("job") or "unknown")
        day = str(record.get("day") or "")
        job = ensure_job(jobs, run_id=run_id, topic=topic, day=day)
        job.outcome_execution_rounds += 1

    for record in summary.get("records", []):
        if not isinstance(record, dict):
            continue
        run_id = str(record.get("run_id") or "unknown")
        topic = str(record.get("job") or "unknown")
        day = str(record.get("day") or "")
        job = ensure_job(jobs, run_id=run_id, topic=topic, day=day)
        next_action = record.get("next_action")
        if isinstance(next_action, str) and next_action in REVIEW_ACTIONS:
            job.review_actions[next_action] += 1
            if should_replace_terminal_action(job, record):
                job.outcome_terminal_action = next_action
                reason = record.get("reason")
                job.outcome_terminal_reason = reason if isinstance(reason, str) else ""
                job.outcome_terminal_cycle_number = int(record.get("cycle_number") or 0)
                job.outcome_terminal_iteration_number = int(
                    record.get("iteration_number") or 0
                )

    for record in summary.get("loop_records", []):
        if not isinstance(record, dict):
            continue
        run_id = str(record.get("run_id") or "unknown")
        topic = str(record.get("job") or "unknown")
        day = str(record.get("day") or "")
        job = ensure_job(jobs, run_id=run_id, topic=topic, day=day)
        next_action = record.get("next_action")
        if isinstance(next_action, str) and next_action in LOOP_ACTIONS:
            job.loop_actions[next_action] += 1

    for record in summary.get("job_metadata_records", []):
        if not isinstance(record, dict):
            continue
        run_id = str(record.get("run_id") or "unknown")
        topic = str(record.get("job") or "unknown")
        day = str(record.get("day") or "")
        job = ensure_job(jobs, run_id=run_id, topic=topic, day=day)
        apply_metadata_record(job, record, runs_dir=runs_dir)

    return sorted(jobs.values(), key=lambda job: (job.day, job.run_id, job.topic))


def safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def optional_number(value: float | None) -> float | str:
    return "" if value is None else value


def row_from_job(job: JobMetrics) -> dict[str, Any]:
    outcome_decisions = sum(job.review_actions.values())
    outcome_rerun = job.review_actions["rerun_execution"]
    outcome_loop_detect = sum(job.loop_actions.values())
    row: dict[str, Any] = {
        "day": job.day,
        "run_id": job.run_id,
        "topic": job.topic,
        "feature_metadata_available": int(job.feature_metadata_available),
        "feature_complexity_hint": optional_number(job.feature_complexity_hint),
        "outcome_execution_rounds": job.outcome_execution_rounds,
        "outcome_decisions": outcome_decisions,
        "outcome_rerun": outcome_rerun,
        "outcome_human_review": job.review_actions["human_review"],
        "outcome_done": job.review_actions["done"],
        "outcome_decision_rerun_rate": safe_rate(outcome_rerun, outcome_decisions),
        "outcome_loop_detect": outcome_loop_detect,
        "outcome_loop_continue": job.loop_actions["continue"],
        "outcome_loop_human_review": job.loop_actions["human_review"],
        "outcome_loop_human_review_rate": safe_rate(
            job.loop_actions["human_review"], outcome_loop_detect
        ),
        "outcome_terminal_action": job.outcome_terminal_action,
        "outcome_terminal_reason": job.outcome_terminal_reason,
        "outcome_terminal_cycle_number": job.outcome_terminal_cycle_number or "",
        "outcome_terminal_iteration_number": job.outcome_terminal_iteration_number or "",
        "metadata_path": job.metadata_path,
        "feature_task": job.feature_task,
    }
    for role in AGENT_ROLES:
        row[f"feature_{role}_model"] = job.agent_models.get(role, "")
        row[f"feature_{role}_variant"] = job.agent_variants.get(role, "")
    return row


def csv_fieldnames() -> list[str]:
    fields = [
        "day",
        "run_id",
        "topic",
        "feature_metadata_available",
        "feature_complexity_hint",
    ]
    for role in AGENT_ROLES:
        fields.extend([f"feature_{role}_model", f"feature_{role}_variant"])
    fields.extend(
        [
            "outcome_execution_rounds",
            "outcome_decisions",
            "outcome_rerun",
            "outcome_human_review",
            "outcome_done",
            "outcome_decision_rerun_rate",
            "outcome_loop_detect",
            "outcome_loop_continue",
            "outcome_loop_human_review",
            "outcome_loop_human_review_rate",
            "outcome_terminal_action",
            "outcome_terminal_reason",
            "outcome_terminal_cycle_number",
            "outcome_terminal_iteration_number",
            "metadata_path",
            "feature_task",
        ]
    )
    return fields


def write_csv(path: Path, jobs: list[JobMetrics]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=csv_fieldnames())
        writer.writeheader()
        for job in jobs:
            writer.writerow(row_from_job(job))


def main() -> int:
    args = parse_args()
    runs_dir = args.runs_dir.expanduser().resolve()
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
    jobs = aggregate_job_metrics(summary, runs_dir=runs_dir)
    if not jobs:
        print(f"No job metrics found under: {runs_dir}", file=sys.stderr)
        return 1

    destination = output_path(runs_dir, args.output_dir.expanduser())
    write_csv(destination, jobs)
    print(destination)
    print(f"rows: {len(jobs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
