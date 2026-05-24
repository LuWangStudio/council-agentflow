from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, TypeVar

from agentflow_core.config import AgentConfig, JobConfig, WorkflowConfig
from agentflow_core.errors import WorkflowError
from agentflow_core.logger import log_step
from agentflow_core.opencode_runner import OpencodeRunner
from agentflow_core.prompts import render_prompt
from agentflow_core.workflow_constants import (
    ALLOWED_AUTONOMY_DECISION_ACTIONS,
    ALLOWED_NEXT_ACTIONS,
    ALLOWED_LOOP_DETECTOR_ACTIONS,
    AUTONOMY_DECISION_REQUIRED_KEYS,
    LOOP_DETECTOR_REQUIRED_KEYS,
    REVIEW_DECISION_REQUIRED_KEYS,
)
from agentflow_core.workflow_io import (
    load_json_text_payload,
    load_text,
)


STEP_OUTPUT_RETRY_LIMIT = 3
T = TypeVar("T")


def render_agent_output_path(
    agent: AgentConfig,
    *,
    topic: str,
    cycle_number: int,
    iteration_number: int,
    job_temp_dir: Path,
) -> Path:
    return render_output_path_template(
        agent.output_path_template,
        agent_key=agent.key,
        agent_output_name=agent.output_name,
        topic=topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )


def render_output_path_template(
    template: str,
    *,
    agent_key: str,
    agent_output_name: str,
    topic: str,
    cycle_number: int,
    iteration_number: int,
    job_temp_dir: Path,
) -> Path:
    rendered = render_prompt(
        template,
        {
            "agent_key": agent_key,
            "agent_output_name": agent_output_name,
            "topic": topic,
            "cycle_number": cycle_number,
            "iteration_number": iteration_number,
            "job_temp_dir": str(job_temp_dir),
        },
    )
    return Path(rendered).expanduser().resolve()


def build_common_prompt_vars(
    workflow_config: WorkflowConfig,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
) -> dict[str, object]:
    task_text = job.task
    if job.human_review:
        task_text = (
            f"{job.task}\n\n"
            "Human feedback (additional constraints or revision guidance for the current job; handle it together with the original task):\n"
            f"{job.human_review}"
        )
    execution_output_path = render_agent_output_path(
        workflow_config.agents["execution"],
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    reviewer_1_output_path = render_agent_output_path(
        workflow_config.agents["reviewer_1"],
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    reviewer_2_output_path = render_agent_output_path(
        workflow_config.agents["reviewer_2"],
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    review_decision_output_path = render_agent_output_path(
        workflow_config.agents["review_decision"],
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    review_decision_review_output_path = (
        render_review_decision_merged_review_output_path(
            workflow_config,
            job=job,
            job_temp_dir=job_temp_dir,
            cycle_number=cycle_number,
            iteration_number=iteration_number,
        )
    )
    adjudication_memory_output_path = render_adjudication_memory_output_path(
        job_temp_dir=job_temp_dir
    )
    loop_detector_output_path = render_agent_output_path(
        workflow_config.agents["loop_detector"],
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    return {
        "topic": job.topic,
        "task": task_text,
        "human_review": "" if job.human_review is None else job.human_review,
        "cycle_number": cycle_number,
        "iteration_number": iteration_number,
        "max_rounds": workflow_config.program.max_rounds,
        "job_temp_dir": str(job_temp_dir),
        "execution_output_path": str(execution_output_path),
        "reviewer_1_output_path": str(reviewer_1_output_path),
        "reviewer_2_output_path": str(reviewer_2_output_path),
        "review_decision_output_path": str(review_decision_output_path),
        "review_decision_review_output_path": str(review_decision_review_output_path),
        "adjudication_memory_output_path": str(adjudication_memory_output_path),
        "loop_detector_output_path": str(loop_detector_output_path),
    }


def render_review_decision_merged_review_output_path(
    workflow_config: WorkflowConfig,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
) -> Path:
    agent = workflow_config.agents["review_decision"]
    template = agent.merged_review_output_path_template
    assert template is not None
    rendered = render_prompt(
        template,
        {
            "agent_key": agent.key,
            "agent_output_name": agent.output_name,
            "topic": job.topic,
            "cycle_number": cycle_number,
            "iteration_number": iteration_number,
            "job_temp_dir": str(job_temp_dir),
        },
    )
    return Path(rendered).expanduser().resolve()


def render_autonomy_decision_report_output_path(
    workflow_config: WorkflowConfig,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
) -> Path:
    agent = workflow_config.agents["autonomy_decision"]
    template = agent.decision_report_output_path_template
    assert template is not None
    return render_output_path_template(
        template,
        agent_key=agent.key,
        agent_output_name=agent.output_name,
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )


def render_adjudication_memory_output_path(*, job_temp_dir: Path) -> Path:
    return (job_temp_dir / "adjudication-memory.latest.txt").expanduser().resolve()


def run_text_agent_step(
    runner: OpencodeRunner,
    *,
    agent_key: str,
    prompt: str,
    output_path: Path,
) -> str:
    return run_agent_step_with_output_retry(
        runner,
        agent_key=agent_key,
        prompt=prompt,
        output_path=output_path,
        loader=lambda path: load_text(path),
        expected_output_description="plain text output file",
    )


def run_agent_step_with_output_retry(
    runner: OpencodeRunner,
    *,
    agent_key: str,
    prompt: str,
    output_path: Path,
    loader: Callable[[Path], T],
    expected_output_description: str,
    extra_output_paths_to_reset: list[Path] | None = None,
) -> T:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    last_error: WorkflowError | None = None
    for attempt in range(1, STEP_OUTPUT_RETRY_LIMIT + 1):
        if output_path.exists():
            output_path.unlink()
        for extra_path in extra_output_paths_to_reset or []:
            if extra_path.exists():
                extra_path.unlink()

        runner.run(agent_key, prompt)

        try:
            return loader(output_path)
        except WorkflowError as exc:
            last_error = exc
            if attempt >= STEP_OUTPUT_RETRY_LIMIT:
                break
            log_step(
                f"Agent {agent_key} did not produce valid {expected_output_description} on attempt {attempt}/{STEP_OUTPUT_RETRY_LIMIT}: {exc}. Retrying same agent."
            )

    assert last_error is not None
    raise WorkflowError(
        f"Agent {agent_key} failed to produce valid {expected_output_description} after {STEP_OUTPUT_RETRY_LIMIT} attempts: {last_error}"
    ) from last_error


def run_execution_step(
    workflow_config: WorkflowConfig,
    runner: OpencodeRunner,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
    review_decision_previous_output_path: Path | None,
    review_decision_previous_review_output_path: Path | None,
) -> tuple[Path, str]:
    agent = workflow_config.agents["execution"]
    agent_output_path = render_agent_output_path(
        agent,
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    prompt = render_prompt(
        agent.prompt_template,
        {
            **build_common_prompt_vars(
                workflow_config,
                job=job,
                job_temp_dir=job_temp_dir,
                cycle_number=cycle_number,
                iteration_number=iteration_number,
            ),
            "agent_key": agent.key,
            "role_name": agent.role_name,
            "agent_output_path": str(agent_output_path),
            "review_decision_previous_output_path": ""
            if review_decision_previous_output_path is None
            else str(review_decision_previous_output_path),
            "review_decision_previous_review_output_path": ""
            if review_decision_previous_review_output_path is None
            else str(review_decision_previous_review_output_path),
        },
    )
    text = run_text_agent_step(
        runner,
        agent_key="execution",
        prompt=prompt,
        output_path=agent_output_path,
    )
    return agent_output_path, text


def run_review_step(
    workflow_config: WorkflowConfig,
    runner: OpencodeRunner,
    *,
    agent_key: str,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
    execution_output_path: Path,
) -> tuple[Path, str]:
    agent = workflow_config.agents[agent_key]
    agent_output_path = render_agent_output_path(
        agent,
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    prompt = render_prompt(
        agent.prompt_template,
        {
            **build_common_prompt_vars(
                workflow_config,
                job=job,
                job_temp_dir=job_temp_dir,
                cycle_number=cycle_number,
                iteration_number=iteration_number,
            ),
            "agent_key": agent.key,
            "role_name": agent.role_name,
            "agent_output_path": str(agent_output_path),
            "execution_response_path": str(execution_output_path),
        },
    )
    text = run_text_agent_step(
        runner,
        agent_key=agent_key,
        prompt=prompt,
        output_path=agent_output_path,
    )
    return agent_output_path, text


def run_review_decision_step(
    workflow_config: WorkflowConfig,
    runner: OpencodeRunner,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
    reviewer_1_output_path: Path,
    reviewer_2_output_path: Path,
) -> tuple[Path, Path, dict[str, object]]:
    agent = workflow_config.agents["review_decision"]
    agent_output_path = render_agent_output_path(
        agent,
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    merged_review_output_path = render_review_decision_merged_review_output_path(
        workflow_config,
        job=job,
        job_temp_dir=job_temp_dir,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
    )
    adjudication_memory_output_path = render_adjudication_memory_output_path(
        job_temp_dir=job_temp_dir
    )
    adjudication_memory_previous_text = ""
    if adjudication_memory_output_path.exists():
        adjudication_memory_previous_text = load_text(adjudication_memory_output_path)
    prompt = render_prompt(
        agent.prompt_template,
        {
            **build_common_prompt_vars(
                workflow_config,
                job=job,
                job_temp_dir=job_temp_dir,
                cycle_number=cycle_number,
                iteration_number=iteration_number,
            ),
            "agent_key": agent.key,
            "role_name": agent.role_name,
            "agent_output_path": str(agent_output_path),
            "merged_review_output_path": str(merged_review_output_path),
            "reviewer_1_output_path": str(reviewer_1_output_path),
            "reviewer_2_output_path": str(reviewer_2_output_path),
            "adjudication_memory_previous_text": adjudication_memory_previous_text,
        },
    )
    payload = run_agent_step_with_output_retry(
        runner,
        agent_key="review_decision",
        prompt=prompt,
        output_path=agent_output_path,
        loader=lambda path: _load_and_validate_review_decision_payload(
            path,
            merged_review_output_path,
            adjudication_memory_output_path,
        ),
        expected_output_description="review decision JSON output file",
        extra_output_paths_to_reset=[
            merged_review_output_path,
            adjudication_memory_output_path,
        ],
    )
    return agent_output_path, merged_review_output_path, payload


def run_autonomy_decision_step(
    workflow_config: WorkflowConfig,
    runner: OpencodeRunner,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
    latest_iteration: dict[str, Any],
    human_review_reason: str,
) -> tuple[Path, Path, dict[str, object]]:
    agent = workflow_config.agents["autonomy_decision"]
    agent_output_path = render_agent_output_path(
        agent,
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    decision_report_output_path = render_autonomy_decision_report_output_path(
        workflow_config,
        job=job,
        job_temp_dir=job_temp_dir,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
    )
    adjudication_memory_output_path = render_adjudication_memory_output_path(
        job_temp_dir=job_temp_dir
    )
    loop_detector = latest_iteration.get("loop_detector")
    latest_loop_detector_output_path = ""
    if isinstance(loop_detector, dict):
        latest_loop_detector_output_path = str(loop_detector.get("output_path", ""))

    prompt = render_prompt(
        agent.prompt_template,
        {
            **build_common_prompt_vars(
                workflow_config,
                job=job,
                job_temp_dir=job_temp_dir,
                cycle_number=cycle_number,
                iteration_number=iteration_number,
            ),
            "agent_key": agent.key,
            "role_name": agent.role_name,
            "agent_output_path": str(agent_output_path),
            "decision_report_output_path": str(decision_report_output_path),
            "human_review_reason": human_review_reason,
            "latest_execution_output_path": str(
                latest_iteration["execution"]["output_path"]
            ),
            "latest_reviewer_1_output_path": str(
                latest_iteration["reviewer_1"]["output_path"]
            ),
            "latest_reviewer_2_output_path": str(
                latest_iteration["reviewer_2"]["output_path"]
            ),
            "latest_review_decision_output_path": str(
                latest_iteration["review_decision"]["output_path"]
            ),
            "latest_merged_review_output_path": str(
                latest_iteration["review_decision"]["merged_review_output_path"]
            ),
            "latest_loop_detector_output_path": latest_loop_detector_output_path,
            "adjudication_memory_output_path": str(adjudication_memory_output_path),
        },
    )
    payload = run_agent_step_with_output_retry(
        runner,
        agent_key="autonomy_decision",
        prompt=prompt,
        output_path=agent_output_path,
        loader=lambda path: _load_and_validate_autonomy_decision_payload(
            path,
            decision_report_output_path,
        ),
        expected_output_description="autonomy decision JSON output file",
        extra_output_paths_to_reset=[decision_report_output_path],
    )
    return agent_output_path, decision_report_output_path, payload


def run_loop_detector_step(
    workflow_config: WorkflowConfig,
    runner: OpencodeRunner,
    *,
    job: JobConfig,
    job_temp_dir: Path,
    cycle_number: int,
    iteration_number: int,
    previous_iteration: dict[str, Any],
    current_iteration: dict[str, Any],
) -> tuple[Path, dict[str, object]]:
    agent = workflow_config.agents["loop_detector"]
    agent_output_path = render_agent_output_path(
        agent,
        topic=job.topic,
        cycle_number=cycle_number,
        iteration_number=iteration_number,
        job_temp_dir=job_temp_dir,
    )
    adjudication_memory_output_path = render_adjudication_memory_output_path(
        job_temp_dir=job_temp_dir
    )

    prompt = render_prompt(
        agent.prompt_template,
        {
            **build_common_prompt_vars(
                workflow_config,
                job=job,
                job_temp_dir=job_temp_dir,
                cycle_number=cycle_number,
                iteration_number=iteration_number,
            ),
            "agent_key": agent.key,
            "role_name": agent.role_name,
            "agent_output_path": str(agent_output_path),
            "previous_execution_output_path": str(
                previous_iteration["execution"]["output_path"]
            ),
            "previous_reviewer_1_output_path": str(
                previous_iteration["reviewer_1"]["output_path"]
            ),
            "previous_reviewer_2_output_path": str(
                previous_iteration["reviewer_2"]["output_path"]
            ),
            "previous_review_decision_output_path": str(
                previous_iteration["review_decision"]["output_path"]
            ),
            "previous_merged_review_output_path": str(
                previous_iteration["review_decision"]["merged_review_output_path"]
            ),
            "current_execution_output_path": str(
                current_iteration["execution"]["output_path"]
            ),
            "current_reviewer_1_output_path": str(
                current_iteration["reviewer_1"]["output_path"]
            ),
            "current_reviewer_2_output_path": str(
                current_iteration["reviewer_2"]["output_path"]
            ),
            "current_review_decision_output_path": str(
                current_iteration["review_decision"]["output_path"]
            ),
            "current_merged_review_output_path": str(
                current_iteration["review_decision"]["merged_review_output_path"]
            ),
            "adjudication_memory_output_path": str(adjudication_memory_output_path),
        },
    )

    payload = run_agent_step_with_output_retry(
        runner,
        agent_key="loop_detector",
        prompt=prompt,
        output_path=agent_output_path,
        loader=_load_and_validate_loop_detector_payload,
        expected_output_description="loop detector JSON output file",
    )
    return agent_output_path, payload


def _load_and_validate_review_decision_payload(
    path: Path,
    merged_review_output_path: Path,
    adjudication_memory_output_path: Path,
) -> dict[str, object]:
    payload = load_json_text_payload(path, REVIEW_DECISION_REQUIRED_KEYS)
    next_action = payload.get("next_action")
    if not isinstance(next_action, str) or next_action not in ALLOWED_NEXT_ACTIONS:
        raise WorkflowError(
            "`next_action` in review decision output must be one of rerun_execution, human_review, done"
        )
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError(
            "`reason` in review decision output must be a non-empty string"
        )
    if not merged_review_output_path.exists():
        raise WorkflowError(
            f"Expected merged review output file does not exist: {merged_review_output_path}"
        )

    merged_review_text = load_text(merged_review_output_path)
    if not merged_review_text.strip():
        raise WorkflowError(
            f"Merged review output file is empty: {merged_review_output_path}"
        )

    _validate_adjudication_memory_output(adjudication_memory_output_path)

    return payload


def _load_and_validate_loop_detector_payload(path: Path) -> dict[str, object]:
    payload = load_json_text_payload(path, LOOP_DETECTOR_REQUIRED_KEYS)
    next_action = payload.get("next_action")
    if (
        not isinstance(next_action, str)
        or next_action not in ALLOWED_LOOP_DETECTOR_ACTIONS
    ):
        raise WorkflowError(
            "`next_action` in loop detector output must be one of continue, human_review"
        )
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError(
            "`reason` in loop detector output must be a non-empty string"
        )
    return payload


def _load_and_validate_autonomy_decision_payload(
    path: Path, decision_report_output_path: Path
) -> dict[str, object]:
    payload = load_json_text_payload(path, AUTONOMY_DECISION_REQUIRED_KEYS)
    extra_keys = set(payload.keys()) - AUTONOMY_DECISION_REQUIRED_KEYS
    if extra_keys:
        raise WorkflowError(
            f"Autonomy decision JSON output contains unsupported keys {sorted(extra_keys)}: {path}"
        )

    next_action = payload.get("next_action")
    if (
        not isinstance(next_action, str)
        or next_action not in ALLOWED_AUTONOMY_DECISION_ACTIONS
    ):
        raise WorkflowError(
            "`next_action` in autonomy decision output must be one of auto_resolve, human_review"
        )
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError(
            "`reason` in autonomy decision output must be a non-empty string"
        )
    resume_feedback = payload.get("resume_feedback")
    if not isinstance(resume_feedback, str):
        raise WorkflowError(
            "`resume_feedback` in autonomy decision output must be a string"
        )
    if next_action == "auto_resolve" and not resume_feedback.strip():
        raise WorkflowError(
            "`resume_feedback` must be non-empty when autonomy decision next_action=auto_resolve"
        )
    if next_action == "human_review" and resume_feedback.strip():
        raise WorkflowError(
            "`resume_feedback` must be empty when autonomy decision next_action=human_review"
        )

    if not decision_report_output_path.exists():
        raise WorkflowError(
            f"Expected autonomy decision report file does not exist: {decision_report_output_path}"
        )
    report_text = load_text(decision_report_output_path)
    if not report_text.strip():
        raise WorkflowError(
            f"Autonomy decision report file is empty: {decision_report_output_path}"
        )
    if not report_text.startswith("# Autonomy Decision Report"):
        raise WorkflowError(
            f"Autonomy decision report must start with '# Autonomy Decision Report': {decision_report_output_path}"
        )

    return payload


def _validate_adjudication_memory_output(path: Path) -> None:
    if not path.exists():
        raise WorkflowError(
            f"Expected adjudication memory output file does not exist: {path}"
        )

    text = load_text(path)
    if not text.strip():
        raise WorkflowError(f"Adjudication memory output file is empty: {path}")
    if not text.startswith("# Adjudication Memory"):
        raise WorkflowError(
            f"Adjudication memory output file must start with '# Adjudication Memory': {path}"
        )
    if "[REJECTED_OR_DEFERRED_ISSUES]" not in text:
        raise WorkflowError(
            f"Adjudication memory output file must contain [REJECTED_OR_DEFERRED_ISSUES]: {path}"
        )

    issue_lines = re.findall(
        r"(?m)^- (?:I\d+ \| (?:rejected|deferred_human)|none|\u65e0)$", text
    )
    if not issue_lines:
        raise WorkflowError(
            f"Adjudication memory output file must contain at least one issue entry or a no-issue marker: {path}"
        )
