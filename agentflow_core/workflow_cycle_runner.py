from __future__ import annotations

from pathlib import Path
import select
import sys
from typing import Any

from agentflow_core.config import JobConfig, WorkflowConfig
from agentflow_core.logger import log_step, log_verbose
from agentflow_core.opencode_runner import OpencodeRunner
from agentflow_core.workflow_steps import (
    render_adjudication_memory_output_path,
    run_execution_step,
    run_loop_detector_step,
    run_review_decision_step,
    run_review_step,
)


FORCE_HUMAN_REVIEW_COMMANDS = {"human", "hr", "\u4eba\u5de5"}


def _poll_force_human_review_command(
    *,
    job: JobConfig,
    cycle_number: int,
    iteration_number: int,
    max_iterations_for_cycle: int,
) -> bool:
    if sys.stdin.closed:
        return False

    try:
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return False

            raw_line = sys.stdin.readline()
            if raw_line == "":
                return False

            command = raw_line.strip().lower()
            if not command:
                continue

            if command in FORCE_HUMAN_REVIEW_COMMANDS:
                log_step(
                    f"Job {job.topic}: cycle {cycle_number} iteration {iteration_number}/{max_iterations_for_cycle} received console command '{command}', forcing human review at iteration boundary"
                )
                return True

            log_step(
                f"Job {job.topic}: cycle {cycle_number} iteration {iteration_number}/{max_iterations_for_cycle} ignored unsupported console command '{command}'"
            )
    except (OSError, ValueError):
        return False


def run_cycle(
    workflow_config: WorkflowConfig,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    max_iterations_for_cycle: int,
    start_iteration_number: int = 1,
    existing_sessions: dict[str, str] | None = None,
    existing_iteration_results: list[dict[str, Any]] | None = None,
    review_decision_previous_output_path: Path | None = None,
    review_decision_previous_review_output_path: Path | None = None,
) -> dict[str, Any]:
    runner = OpencodeRunner(
        program=workflow_config.program, agents=workflow_config.agents
    )
    if existing_sessions is not None:
        runner.sessions = dict(existing_sessions)

    iteration_results: list[dict[str, Any]] = (
        list(existing_iteration_results)
        if existing_iteration_results is not None
        else []
    )
    iteration_number = start_iteration_number

    while iteration_number <= max_iterations_for_cycle:
        log_step(
            f"Job {job.topic}: cycle {cycle_number} iteration {iteration_number}/{max_iterations_for_cycle} execution"
        )
        execution_output_path, execution_text = run_execution_step(
            workflow_config,
            runner,
            job=job,
            job_temp_dir=job_temp_dir,
            cycle_number=cycle_number,
            iteration_number=iteration_number,
            review_decision_previous_output_path=review_decision_previous_output_path,
            review_decision_previous_review_output_path=review_decision_previous_review_output_path,
        )
        log_verbose(
            f"Execution output cycle={cycle_number} iteration={iteration_number}",
            execution_text,
        )

        reviewer_1_output_path, reviewer_1_text = run_review_step(
            workflow_config,
            runner,
            agent_key="reviewer_1",
            job=job,
            job_temp_dir=job_temp_dir,
            cycle_number=cycle_number,
            iteration_number=iteration_number,
            execution_output_path=execution_output_path,
        )
        reviewer_2_output_path, reviewer_2_text = run_review_step(
            workflow_config,
            runner,
            agent_key="reviewer_2",
            job=job,
            job_temp_dir=job_temp_dir,
            cycle_number=cycle_number,
            iteration_number=iteration_number,
            execution_output_path=execution_output_path,
        )
        (
            review_decision_output_path,
            review_decision_review_output_path,
            review_decision_payload,
        ) = run_review_decision_step(
            workflow_config,
            runner,
            job=job,
            job_temp_dir=job_temp_dir,
            cycle_number=cycle_number,
            iteration_number=iteration_number,
            reviewer_1_output_path=reviewer_1_output_path,
            reviewer_2_output_path=reviewer_2_output_path,
        )
        log_verbose(
            f"Review decision JSON cycle={cycle_number} iteration={iteration_number}",
            review_decision_output_path.read_text(encoding="utf-8"),
        )
        log_verbose(
            f"Review decision merged review cycle={cycle_number} iteration={iteration_number}",
            review_decision_review_output_path.read_text(encoding="utf-8"),
        )

        iteration_results.append(
            {
                "iteration_number": iteration_number,
                "execution": {
                    "output_path": str(execution_output_path),
                    "text": execution_text,
                },
                "reviewer_1": {
                    "output_path": str(reviewer_1_output_path),
                    "text": reviewer_1_text,
                },
                "reviewer_2": {
                    "output_path": str(reviewer_2_output_path),
                    "text": reviewer_2_text,
                },
                "review_decision": {
                    "output_path": str(review_decision_output_path),
                    "merged_review_output_path": str(
                        review_decision_review_output_path
                    ),
                    "adjudication_memory_output_path": str(
                        render_adjudication_memory_output_path(
                            job_temp_dir=job_temp_dir
                        )
                    ),
                    "payload": review_decision_payload,
                },
                "loop_detector": None,
            }
        )

        review_decision_previous_output_path = review_decision_output_path
        review_decision_previous_review_output_path = review_decision_review_output_path

        next_action = review_decision_payload["next_action"]
        reason = str(review_decision_payload["reason"])
        log_step(
            f"Job {job.topic}: cycle {cycle_number} iteration {iteration_number}/{max_iterations_for_cycle} review decision next_action={next_action} reason={reason}"
        )

        if _poll_force_human_review_command(
            job=job,
            cycle_number=cycle_number,
            iteration_number=iteration_number,
            max_iterations_for_cycle=max_iterations_for_cycle,
        ):
            forced_reason = "Forced by console command at iteration boundary: human review requested"
            log_step(
                f"Job {job.topic}: cycle {cycle_number} iteration {iteration_number}/{max_iterations_for_cycle} forcing next_action=human_review reason={forced_reason}"
            )
            return {
                "cycle_number": cycle_number,
                "sessions": runner.sessions,
                "iterations": iteration_results,
                "next_action": "human_review",
                "reason": forced_reason,
            }

        if (
            iteration_number >= 4
            and iteration_number % 2 == 0
            and len(iteration_results) >= 2
            and next_action == "rerun_execution"
            and iteration_results[-2]["review_decision"]["payload"]["next_action"]
            == "rerun_execution"
        ):
            loop_detector_output_path, loop_detector_payload = run_loop_detector_step(
                workflow_config,
                runner,
                job=job,
                job_temp_dir=job_temp_dir,
                cycle_number=cycle_number,
                iteration_number=iteration_number,
                previous_iteration=iteration_results[-2],
                current_iteration=iteration_results[-1],
            )
            iteration_results[-1]["loop_detector"] = {
                "output_path": str(loop_detector_output_path),
                "payload": loop_detector_payload,
            }
            loop_detector_next_action = loop_detector_payload["next_action"]
            loop_detector_reason = str(loop_detector_payload["reason"])
            log_step(
                f"Job {job.topic}: cycle {cycle_number} iteration {iteration_number}/{max_iterations_for_cycle} loop detector next_action={loop_detector_next_action} reason={loop_detector_reason}"
            )
            log_verbose(
                f"Loop detector JSON cycle={cycle_number} iteration={iteration_number}",
                loop_detector_output_path.read_text(encoding="utf-8"),
            )

            if loop_detector_next_action == "human_review":
                return {
                    "cycle_number": cycle_number,
                    "sessions": runner.sessions,
                    "iterations": iteration_results,
                    "next_action": "human_review",
                    "reason": loop_detector_reason,
                }

        if next_action == "done":
            log_step(
                f"Job {job.topic}: cycle {cycle_number} completed; current agent sessions will be reset before the next cycle"
            )
            return {
                "cycle_number": cycle_number,
                "sessions": runner.sessions,
                "iterations": iteration_results,
                "next_action": next_action,
                "reason": reason,
            }

        if next_action == "human_review":
            return {
                "cycle_number": cycle_number,
                "sessions": runner.sessions,
                "iterations": iteration_results,
                "next_action": next_action,
                "reason": reason,
            }

        iteration_number += 1

    reason = (
        f"Reached max_iterations_per_cycle={max_iterations_for_cycle}; "
        "automatic loop stopped to avoid infinite rerun"
    )
    log_step(f"Job {job.topic}: {reason}")
    return {
        "cycle_number": cycle_number,
        "sessions": runner.sessions,
        "iterations": iteration_results,
        "next_action": "human_review",
        "reason": reason,
    }
