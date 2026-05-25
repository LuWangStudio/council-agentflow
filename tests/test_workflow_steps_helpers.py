from __future__ import annotations

from pathlib import Path
from typing import Callable, TypeVar

import pytest

from agentflow_core.config import AgentConfig, JobConfig, ProgramConfig, WorkflowConfig
from agentflow_core.errors import WorkflowError
from agentflow_core.workflow_steps import (
    STEP_OUTPUT_RETRY_LIMIT,
    build_common_prompt_vars,
    render_adjudication_memory_output_path,
    render_agent_output_path,
    render_autonomy_decision_report_output_path,
    render_output_path_template,
    render_review_decision_merged_review_output_path,
    run_agent_step_with_output_retry,
)


T = TypeVar("T")


class FakeRunner:
    def __init__(self, *, observed_paths: list[Path] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.path_states_before_run: list[dict[Path, bool]] = []
        self._observed_paths = observed_paths or []

    def run(self, agent_key: str, prompt: str) -> None:
        self.calls.append((agent_key, prompt))
        self.path_states_before_run.append(
            {path: path.exists() for path in self._observed_paths}
        )


def make_agent(
    key: str,
    *,
    output_name: str | None = None,
    output_path_template: str = "${job_temp_dir}/${agent_output_name}-cycle-${cycle_number}-iteration-${iteration_number}.txt",
    merged_review_output_path_template: str | None = None,
    decision_report_output_path_template: str | None = None,
) -> AgentConfig:
    return AgentConfig(
        key=key,
        role_name=f"{key} role",
        output_name=output_name or key.replace("_", "-"),
        model="test/model",
        variant=None,
        prompt_template="Prompt for ${task}",
        output_path_template=output_path_template,
        merged_review_output_path_template=merged_review_output_path_template,
        decision_report_output_path_template=decision_report_output_path_template,
    )


def make_workflow_config(tmp_path: Path) -> WorkflowConfig:
    agents = {
        "execution": make_agent("execution", output_name="execution-out"),
        "reviewer_1": make_agent("reviewer_1", output_name="reviewer-one"),
        "reviewer_2": make_agent("reviewer_2", output_name="reviewer-two"),
        "review_decision": make_agent(
            "review_decision",
            output_name="review-decision",
            merged_review_output_path_template="${job_temp_dir}/merged/${topic}/${agent_output_name}-c${cycle_number}-i${iteration_number}.review.txt",
        ),
        "autonomy_decision": make_agent(
            "autonomy_decision",
            output_name="autonomy-decision",
            decision_report_output_path_template="${job_temp_dir}/reports/${topic}/${agent_output_name}-c${cycle_number}-i${iteration_number}.report.md",
        ),
        "loop_detector": make_agent("loop_detector", output_name="loop-detector"),
    }
    return WorkflowConfig(
        config_path=tmp_path / "workflow.yaml",
        jobs_path=tmp_path / "jobs.yaml",
        prompt_pack_dir=tmp_path / "prompts" / "implementation",
        program=ProgramConfig(
            opencode_bin="opencode",
            attach_url="http://localhost:4096",
            default_model="test/model",
            default_variant=None,
            prompt_pack="implementation",
            prompt_pack_path=None,
            max_rounds=2,
            max_iterations_per_cycle=5,
            temp_dir="temp",
            write_back=False,
        ),
        agents=agents,
        jobs=[],
    )


def make_job(*, human_review: str | None = None) -> JobConfig:
    return JobConfig(
        index=0,
        topic="unit-topic",
        task="Original task text.",
        status="pending",
        human_review=human_review,
    )


def assert_under(path_text: object, parent: Path) -> None:
    assert isinstance(path_text, str)
    assert Path(path_text).is_relative_to(parent.resolve())


def test_render_output_path_template_covers_all_supported_variables(
    tmp_path: Path,
) -> None:
    job_temp_dir = tmp_path / "job-temp"

    path = render_output_path_template(
        "${job_temp_dir}/${topic}/${agent_key}/${agent_output_name}/c${cycle_number}-i${iteration_number}.txt",
        agent_key="reviewer_1",
        agent_output_name="reviewer-one",
        topic="topic-a",
        cycle_number=3,
        iteration_number=4,
        job_temp_dir=job_temp_dir,
    )

    assert path == (
        job_temp_dir / "topic-a" / "reviewer_1" / "reviewer-one" / "c3-i4.txt"
    ).resolve()


def test_render_agent_output_path_uses_agent_output_name(tmp_path: Path) -> None:
    agent = make_agent(
        "execution",
        output_name="custom-execution-output",
        output_path_template="${job_temp_dir}/${agent_key}-${agent_output_name}.txt",
    )

    path = render_agent_output_path(
        agent,
        topic="ignored-topic",
        cycle_number=1,
        iteration_number=2,
        job_temp_dir=tmp_path,
    )

    assert path == (tmp_path / "execution-custom-execution-output.txt").resolve()


def test_render_review_decision_merged_review_output_path(tmp_path: Path) -> None:
    workflow_config = make_workflow_config(tmp_path)
    job_temp_dir = tmp_path / "job-temp"
    job = make_job()

    path = render_review_decision_merged_review_output_path(
        workflow_config,
        job=job,
        job_temp_dir=job_temp_dir,
        cycle_number=5,
        iteration_number=6,
    )

    assert path == (
        job_temp_dir
        / "merged"
        / "unit-topic"
        / "review-decision-c5-i6.review.txt"
    ).resolve()


def test_render_autonomy_decision_report_output_path(tmp_path: Path) -> None:
    workflow_config = make_workflow_config(tmp_path)
    job_temp_dir = tmp_path / "job-temp"
    job = make_job()

    path = render_autonomy_decision_report_output_path(
        workflow_config,
        job=job,
        job_temp_dir=job_temp_dir,
        cycle_number=7,
        iteration_number=8,
    )

    assert path == (
        job_temp_dir
        / "reports"
        / "unit-topic"
        / "autonomy-decision-c7-i8.report.md"
    ).resolve()


def test_render_adjudication_memory_output_path_points_inside_job_temp_dir(
    tmp_path: Path,
) -> None:
    job_temp_dir = tmp_path / "job-temp"

    path = render_adjudication_memory_output_path(job_temp_dir=job_temp_dir)

    assert path == (job_temp_dir / "adjudication-memory.latest.txt").resolve()


def test_build_common_prompt_vars_without_human_review_uses_original_task(
    tmp_path: Path,
) -> None:
    workflow_config = make_workflow_config(tmp_path)
    job_temp_dir = tmp_path / "job-temp"

    prompt_vars = build_common_prompt_vars(
        workflow_config,
        job=make_job(),
        job_temp_dir=job_temp_dir,
        cycle_number=1,
        iteration_number=2,
    )

    assert prompt_vars["topic"] == "unit-topic"
    assert prompt_vars["task"] == "Original task text."
    assert prompt_vars["human_review"] == ""
    assert prompt_vars["cycle_number"] == 1
    assert prompt_vars["iteration_number"] == 2
    assert prompt_vars["max_rounds"] == 2
    for key in (
        "execution_output_path",
        "reviewer_1_output_path",
        "reviewer_2_output_path",
        "review_decision_output_path",
        "review_decision_review_output_path",
        "adjudication_memory_output_path",
        "loop_detector_output_path",
    ):
        assert_under(prompt_vars[key], job_temp_dir)
    assert Path(str(prompt_vars["execution_output_path"])).name == (
        "execution-out-cycle-1-iteration-2.txt"
    )
    assert Path(str(prompt_vars["review_decision_review_output_path"])).name == (
        "review-decision-c1-i2.review.txt"
    )


def test_build_common_prompt_vars_with_human_review_appends_feedback(
    tmp_path: Path,
) -> None:
    workflow_config = make_workflow_config(tmp_path)
    human_review = "Please prioritize path rendering helper coverage."

    prompt_vars = build_common_prompt_vars(
        workflow_config,
        job=make_job(human_review=human_review),
        job_temp_dir=tmp_path / "job-temp",
        cycle_number=2,
        iteration_number=3,
    )

    assert str(prompt_vars["task"]).startswith("Original task text.\n\nHuman feedback")
    assert human_review in str(prompt_vars["task"])
    assert prompt_vars["human_review"] == human_review


def run_with_loader(
    runner: FakeRunner,
    output_path: Path,
    loader: Callable[[Path], T],
    *,
    extra_output_paths_to_reset: list[Path] | None = None,
) -> T:
    return run_agent_step_with_output_retry(
        runner,  # type: ignore[arg-type]
        agent_key="execution",
        prompt="Do the task.",
        output_path=output_path,
        loader=loader,
        expected_output_description="test output",
        extra_output_paths_to_reset=extra_output_paths_to_reset,
    )


def test_run_agent_step_with_output_retry_succeeds_on_first_attempt(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    output_path = tmp_path / "nested" / "output.txt"

    result = run_with_loader(runner, output_path, loader=lambda path: f"loaded:{path.name}")

    assert result == "loaded:output.txt"
    assert runner.calls == [("execution", "Do the task.")]
    assert output_path.parent.is_dir()


def test_run_agent_step_with_output_retry_retries_after_loader_error(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    output_path = tmp_path / "output.txt"
    loader_calls = 0

    def loader(path: Path) -> str:
        nonlocal loader_calls
        loader_calls += 1
        if loader_calls == 1:
            raise WorkflowError("not ready yet")
        return f"loaded on attempt {loader_calls}: {path.name}"

    result = run_with_loader(runner, output_path, loader=loader)

    assert result == "loaded on attempt 2: output.txt"
    assert loader_calls == 2
    assert runner.calls == [
        ("execution", "Do the task."),
        ("execution", "Do the task."),
    ]


def test_run_agent_step_with_output_retry_fails_after_retries_exhausted(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()

    def loader(path: Path) -> str:
        raise WorkflowError(f"still invalid: {path.name}")

    with pytest.raises(WorkflowError, match="failed to produce valid test output"):
        run_with_loader(runner, tmp_path / "output.txt", loader=loader)

    assert len(runner.calls) == STEP_OUTPUT_RETRY_LIMIT


def test_run_agent_step_with_output_retry_removes_stale_outputs_before_retry(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "output.txt"
    extra_output_path = tmp_path / "extra.txt"
    output_path.write_text("old primary", encoding="utf-8")
    extra_output_path.write_text("old extra", encoding="utf-8")
    runner = FakeRunner(observed_paths=[output_path, extra_output_path])
    loader_calls = 0

    def loader(path: Path) -> str:
        nonlocal loader_calls
        loader_calls += 1
        if loader_calls == 1:
            path.write_text("stale primary from failed attempt", encoding="utf-8")
            extra_output_path.write_text("stale extra from failed attempt", encoding="utf-8")
            raise WorkflowError("bad output")
        return "valid output"

    result = run_with_loader(
        runner,
        output_path,
        loader=loader,
        extra_output_paths_to_reset=[extra_output_path],
    )

    assert result == "valid output"
    assert runner.path_states_before_run == [
        {output_path: False, extra_output_path: False},
        {output_path: False, extra_output_path: False},
    ]
    assert not output_path.exists()
    assert not extra_output_path.exists()
