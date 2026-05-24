from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agentflow_core.config import (
    JobConfig,
    WorkflowConfig,
    reload_job_from_jobs_file,
    update_job_status,
)
from agentflow_core.logger import log_step
from agentflow_core.opencode_runner import OpencodeRunner
from agentflow_core.workflow_cycle_runner import run_cycle
from agentflow_core.workflow_human_review import (
    clear_runtime_human_review,
    prompt_human_review_action,
)
from agentflow_core.workflow_steps import run_autonomy_decision_step
from agentflow_core.workflow_io import reset_job_temp_dir


def build_final_result(
    *,
    workflow_config: WorkflowConfig,
    job: JobConfig,
    run_id: str,
    job_temp_dir: Path,
    final_status: str,
    cycle_results: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "topic": job.topic,
        "run_id": run_id,
        "status": final_status,
        "reason": reason,
        "temp_dir": str(job_temp_dir),
        "cycles": cycle_results,
        "config_file": str(workflow_config.config_path),
        "jobs_file": str(workflow_config.jobs_path),
    }


def _resume_cycle_from_latest_iteration(
    workflow_config: WorkflowConfig,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    max_iterations_for_cycle: int,
    cycle_result: dict[str, Any],
) -> dict[str, Any]:
    latest_iteration = cycle_result["iterations"][-1]
    return run_cycle(
        workflow_config,
        job=job,
        job_temp_dir=job_temp_dir,
        cycle_number=cycle_number,
        max_iterations_for_cycle=max_iterations_for_cycle,
        start_iteration_number=latest_iteration["iteration_number"] + 1,
        existing_sessions=cycle_result["sessions"],
        existing_iteration_results=cycle_result["iterations"],
        review_decision_previous_output_path=Path(
            latest_iteration["review_decision"]["output_path"]
        ),
        review_decision_previous_review_output_path=Path(
            latest_iteration["review_decision"]["merged_review_output_path"]
        ),
    )


def run_job(
    workflow_config: WorkflowConfig,
    *,
    job: JobConfig,
    run_id: str,
) -> dict[str, Any]:
    current_job = clear_runtime_human_review(job)
    root = workflow_config.jobs_path.parent
    temp_root = root / workflow_config.program.temp_dir / "runs" / run_id
    job_temp_dir = temp_root / current_job.topic

    log_step(
        f"Starting job topic={current_job.topic} status={current_job.status} run_id={run_id}"
    )
    update_job_status(workflow_config, job_index=current_job.index, status="running")
    reset_job_temp_dir(job_temp_dir)

    cycle_results: list[dict[str, Any]] = []
    for cycle_number in range(1, workflow_config.program.max_rounds + 1):
        log_step(
            f"Job {current_job.topic}: starting cycle {cycle_number}/{workflow_config.program.max_rounds}"
        )
        max_iterations_for_cycle = workflow_config.program.max_iterations_per_cycle
        cycle_result = run_cycle(
            workflow_config,
            job=current_job,
            job_temp_dir=job_temp_dir,
            cycle_number=cycle_number,
            max_iterations_for_cycle=max_iterations_for_cycle,
        )

        while True:
            if cycle_result["next_action"] == "human_review":
                decision_job = clear_runtime_human_review(current_job)
                latest_iteration = (
                    cycle_result["iterations"][-1]
                    if cycle_result["iterations"]
                    else None
                )
                should_skip_autonomy = str(cycle_result["reason"]).startswith(
                    "Forced by console command"
                )

                if latest_iteration is not None and not should_skip_autonomy:
                    autonomy_runner = OpencodeRunner(
                        program=workflow_config.program,
                        agents=workflow_config.agents,
                    )
                    autonomy_runner.sessions = dict(cycle_result["sessions"])
                    (
                        autonomy_output_path,
                        autonomy_report_output_path,
                        autonomy_payload,
                    ) = run_autonomy_decision_step(
                        workflow_config,
                        autonomy_runner,
                        job=decision_job,
                        job_temp_dir=job_temp_dir,
                        cycle_number=cycle_number,
                        iteration_number=latest_iteration["iteration_number"],
                        latest_iteration=latest_iteration,
                        human_review_reason=str(cycle_result["reason"]),
                    )
                    cycle_result["sessions"] = autonomy_runner.sessions
                    latest_iteration["autonomy_decision"] = {
                        "output_path": str(autonomy_output_path),
                        "report_output_path": str(autonomy_report_output_path),
                        "payload": autonomy_payload,
                    }
                    autonomy_next_action = autonomy_payload["next_action"]
                    autonomy_reason = str(autonomy_payload["reason"])
                    log_step(
                        f"Job {decision_job.topic}: cycle {cycle_number} iteration {latest_iteration['iteration_number']} autonomy decision next_action={autonomy_next_action} reason={autonomy_reason}"
                    )

                    if autonomy_next_action == "auto_resolve":
                        resume_feedback = str(autonomy_payload["resume_feedback"])
                        current_job = replace(
                            decision_job,
                            human_review=(
                                "Autonomy decision feedback (generated automatically under policy; treat as additional constraints or revision guidance for this round):\n"
                                f"{resume_feedback}\n\n"
                                f"Autonomy decision report: {autonomy_report_output_path}"
                            ),
                        )
                        if latest_iteration["iteration_number"] >= max_iterations_for_cycle:
                            max_iterations_for_cycle = (
                                latest_iteration["iteration_number"] + 1
                            )
                            log_step(
                                f"Job {decision_job.topic}: extended cycle {cycle_number} iteration limit to {max_iterations_for_cycle} for autonomy auto_resolve"
                            )
                        cycle_result = _resume_cycle_from_latest_iteration(
                            workflow_config,
                            job=current_job,
                            job_temp_dir=job_temp_dir,
                            cycle_number=cycle_number,
                            max_iterations_for_cycle=max_iterations_for_cycle,
                            cycle_result=cycle_result,
                        )
                        continue

                current_job = decision_job
                human_review_action = prompt_human_review_action(
                    workflow_config=workflow_config,
                    job=current_job,
                    job_temp_dir=job_temp_dir,
                    cycle_result=cycle_result,
                    cycle_number=cycle_number,
                    max_rounds=workflow_config.program.max_rounds,
                    max_iterations_for_cycle=max_iterations_for_cycle,
                )
                if human_review_action["action"] == "resume_iteration":
                    current_job = human_review_action["job"]
                    max_iterations_for_cycle = human_review_action[
                        "max_iterations_for_cycle"
                    ]
                    cycle_result = _resume_cycle_from_latest_iteration(
                        workflow_config,
                        job=current_job,
                        job_temp_dir=job_temp_dir,
                        cycle_number=cycle_number,
                        max_iterations_for_cycle=max_iterations_for_cycle,
                        cycle_result=cycle_result,
                    )
                    continue

                cycle_results.append(cycle_result)
                if human_review_action["action"] == "advance_cycle":
                    current_job = clear_runtime_human_review(
                        reload_job_from_jobs_file(
                            workflow_config, job_index=current_job.index
                        )
                    )
                    log_step(
                        f"Job {current_job.topic}: human review completed without additional feedback; advancing to cycle {cycle_number + 1}/{workflow_config.program.max_rounds}"
                    )
                    break

                result = build_final_result(
                    workflow_config=workflow_config,
                    job=current_job,
                    run_id=run_id,
                    job_temp_dir=job_temp_dir,
                    final_status="needs_human_review",
                    cycle_results=cycle_results,
                    reason=str(cycle_result["reason"]),
                )
                update_job_status(
                    workflow_config,
                    job_index=current_job.index,
                    status="needs_human_review",
                )
                log_step(
                    f"Job {current_job.topic}: requires human review, reason={cycle_result['reason']}"
                )
                log_step(f"Job {current_job.topic}: final result {result}")
                return result

            if cycle_result["next_action"] == "done":
                current_job = clear_runtime_human_review(current_job)
                human_review_action = prompt_human_review_action(
                    workflow_config=workflow_config,
                    job=current_job,
                    job_temp_dir=job_temp_dir,
                    cycle_result=cycle_result,
                    cycle_number=cycle_number,
                    max_rounds=workflow_config.program.max_rounds,
                    max_iterations_for_cycle=max_iterations_for_cycle,
                    cycle_completion_gate=True,
                )
                if human_review_action["action"] == "resume_iteration":
                    current_job = human_review_action["job"]
                    max_iterations_for_cycle = human_review_action[
                        "max_iterations_for_cycle"
                    ]
                    cycle_result = _resume_cycle_from_latest_iteration(
                        workflow_config,
                        job=current_job,
                        job_temp_dir=job_temp_dir,
                        cycle_number=cycle_number,
                        max_iterations_for_cycle=max_iterations_for_cycle,
                        cycle_result=cycle_result,
                    )
                    continue

                cycle_results.append(cycle_result)
                if cycle_number < workflow_config.program.max_rounds:
                    current_job = clear_runtime_human_review(
                        reload_job_from_jobs_file(
                            workflow_config, job_index=current_job.index
                        )
                    )
                    log_step(
                        f"Job {current_job.topic}: advancing to next cycle after human-reviewed cycle {cycle_number}/{workflow_config.program.max_rounds} completion"
                    )
                break

            cycle_results.append(cycle_result)
            break

    result = build_final_result(
        workflow_config=workflow_config,
        job=current_job,
        run_id=run_id,
        job_temp_dir=job_temp_dir,
        final_status="done",
        cycle_results=cycle_results,
        reason="max_rounds_completed",
    )
    update_job_status(workflow_config, job_index=current_job.index, status="done")
    log_step(f"Job {current_job.topic}: final result {result}")
    log_step(f"Job {current_job.topic}: completed all cycles")
    return result


def run_workflow(workflow_config: WorkflowConfig) -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_step(f"Starting workflow run_id={run_id}")

    skipped_jobs: list[dict[str, Any]] = []
    completed_jobs: list[dict[str, Any]] = []
    failed_jobs: list[dict[str, Any]] = []

    for job in workflow_config.jobs:
        if job.status in {"done", "needs_human_review"}:
            skipped_jobs.append(
                {"topic": job.topic, "status": job.status, "reason": "terminal_status"}
            )
            log_step(f"Skipping job topic={job.topic} because status={job.status}")
            continue

        try:
            result = run_job(workflow_config, job=job, run_id=run_id)
        except Exception as exc:
            update_job_status(workflow_config, job_index=job.index, status="failed")
            failed_jobs.append(
                {"topic": job.topic, "status": "failed", "error": str(exc)}
            )
            log_step(f"Job {job.topic} failed: {exc}")
            continue

        completed_jobs.append(result)

    overall_status = "completed" if not failed_jobs else "completed_with_failures"
    return {
        "run_id": run_id,
        "status": overall_status,
        "config_file": str(workflow_config.config_path),
        "jobs_file": str(workflow_config.jobs_path),
        "temp_dir": str(
            workflow_config.jobs_path.parent
            / workflow_config.program.temp_dir
            / "runs"
            / run_id
        ),
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "skipped_jobs": skipped_jobs,
        "summary": {
            "completed": len(completed_jobs),
            "failed": len(failed_jobs),
            "skipped": len(skipped_jobs),
        },
    }
