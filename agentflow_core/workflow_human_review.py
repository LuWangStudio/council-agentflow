from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from agentflow_core.config import JobConfig, WorkflowConfig, reload_job_from_jobs_file
from agentflow_core.logger import log_step


def clear_runtime_human_review(job: JobConfig) -> JobConfig:
    return replace(job, human_review=None)


def prompt_human_review_action(
    *,
    workflow_config: WorkflowConfig,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_result: dict[str, Any],
    cycle_number: int,
    max_rounds: int,
    max_iterations_for_cycle: int,
    cycle_completion_gate: bool = False,
) -> dict[str, Any]:
    decision_output_path, merged_review_output_path = _latest_iteration_review_paths(
        cycle_result
    )
    if cycle_completion_gate:
        log_step(
            f"Job {job.topic}: cycle {cycle_number}/{max_rounds} completed; waiting for optional human review before finishing or advancing, reason={cycle_result['reason']}"
        )
    else:
        log_step(
            f"Job {job.topic}: human review requested at cycle {cycle_number}/{max_rounds}, reason={cycle_result['reason']}"
        )
    log_step(
        f"Job {job.topic}: review workspace before continuing. job_temp_dir={job_temp_dir}"
    )
    log_step(f"Job {job.topic}: jobs file path: {workflow_config.jobs_path}")
    if merged_review_output_path:
        log_step(
            f"Job {job.topic}: merged review path for human review: {merged_review_output_path}"
        )
    if decision_output_path:
        log_step(f"Job {job.topic}: review decision JSON path: {decision_output_path}")

    while True:
        prompt_message = (
            "This cycle has completed. Have you added or updated human_review and want to use it as feedback for another iteration in this cycle? Enter y/n: "
            if cycle_completion_gate
            else "Have you added or updated human_review and want to use it as feedback for this round? Enter y/n: "
        )
        has_human_feedback = _prompt_yes_no(
            prompt_message,
            job=job,
        )
        if has_human_feedback:
            updated_job = reload_job_from_jobs_file(
                workflow_config, job_index=job.index
            )
            if not updated_job.human_review:
                log_step(
                    f"Job {job.topic}: the current job in the jobs YAML file has no usable human_review field; add feedback before choosing yes"
                )
                continue

            next_max_iterations = max_iterations_for_cycle
            current_iteration_count = len(cycle_result["iterations"])
            if current_iteration_count >= max_iterations_for_cycle:
                extra_iterations = _prompt_additional_iterations(job=job)
                next_max_iterations += extra_iterations
                log_step(
                    f"Job {job.topic}: extended cycle {cycle_number} iteration limit from {max_iterations_for_cycle} to {next_max_iterations}"
                )

            log_step(
                f"Job {job.topic}: loaded human_review feedback from jobs YAML for this run; continuing current cycle with updated task context"
            )
            return {
                "action": "resume_iteration",
                "job": updated_job,
                "max_iterations_for_cycle": next_max_iterations,
            }

        if cycle_completion_gate:
            log_step(
                f"Job {job.topic}: no human_review feedback confirmed for completed cycle {cycle_number}; completing current cycle"
            )
            return {"action": "complete_cycle"}

        if cycle_number >= max_rounds:
            log_step(
                f"Job {job.topic}: current cycle is already the last cycle; cannot advance to another cycle without human feedback"
            )
            return {"action": "stop"}

        should_advance_cycle = _prompt_yes_no(
            "No human feedback is available. Advance to the next cycle? Enter y to continue to the next cycle, or n to stop and mark the job as needs_human_review: ",
            job=job,
        )
        if should_advance_cycle:
            return {"action": "advance_cycle"}
        return {"action": "stop"}


def _prompt_yes_no(message: str, *, job: JobConfig) -> bool:
    while True:
        try:
            answer = input(message).strip().lower()
        except (EOFError, KeyboardInterrupt):
            log_step(
                f"Job {job.topic}: human review input interrupted; treating it as no"
            )
            return False

        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False

        log_step(
            f"Job {job.topic}: invalid human review input '{answer}', expected y/yes or n/no"
        )


def _prompt_additional_iterations(*, job: JobConfig) -> int:
    while True:
        try:
            raw_value = input(
                "The current cycle has reached its iteration limit. Enter the number of extra iterations to add as a positive integer: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            log_step(
                f"Job {job.topic}: human review input interrupted while requesting extra iterations"
            )
            continue

        try:
            value = int(raw_value)
        except ValueError:
            log_step(
                f"Job {job.topic}: invalid additional iteration value '{raw_value}', expected a positive integer"
            )
            continue

        if value <= 0:
            log_step(
                f"Job {job.topic}: additional iteration value must be > 0, got {value}"
            )
            continue
        return value


def _latest_iteration_review_paths(cycle_result: dict[str, Any]) -> tuple[str, str]:
    latest_iteration = (
        cycle_result["iterations"][-1] if cycle_result["iterations"] else None
    )
    if latest_iteration is None:
        return "", ""

    review_decision = latest_iteration.get("review_decision", {})
    return (
        str(review_decision.get("output_path", "")),
        str(review_decision.get("merged_review_output_path", "")),
    )
