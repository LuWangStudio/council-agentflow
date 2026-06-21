from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentflow_core.config import (
    AgentConfig,
    JobConfig,
    JobMetadataConfig,
    ProgramConfig,
    WorkflowConfig,
)
from agentflow_core.workflow_runner import run_job


def make_agent(key: str, *, variant: str | None = None) -> AgentConfig:
    return AgentConfig(
        key=key,
        role_name=f"{key} role",
        output_name=key.replace("_", "-"),
        model="test/model",
        variant=variant,
        prompt_template="Prompt for ${task}",
        output_path_template="${job_temp_dir}/${agent_output_name}-cycle-${cycle_number}-iteration-${iteration_number}.txt",
        merged_review_output_path_template=(
            "${job_temp_dir}/${agent_output_name}-cycle-${cycle_number}-iteration-${iteration_number}.review.txt"
            if key == "review_decision"
            else None
        ),
        decision_report_output_path_template=(
            "${job_temp_dir}/${agent_output_name}-cycle-${cycle_number}-iteration-${iteration_number}.report.md"
            if key == "autonomy_decision"
            else None
        ),
    )


def make_workflow_config(tmp_path: Path) -> WorkflowConfig:
    agents = {
        "execution": make_agent("execution", variant="xhigh"),
        "reviewer_1": make_agent("reviewer_1", variant="xhigh"),
        "reviewer_2": make_agent("reviewer_2", variant="xhigh"),
        "review_decision": make_agent("review_decision", variant="xhigh"),
        "autonomy_decision": make_agent("autonomy_decision", variant="xhigh"),
        "loop_detector": make_agent("loop_detector", variant="medium"),
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
            max_rounds=1,
            max_iterations_per_cycle=5,
            temp_dir="temp",
            write_back=False,
        ),
        agents=agents,
        jobs=[],
    )


def make_job() -> JobConfig:
    return JobConfig(
        index=0,
        topic="metadata-job",
        task="Collect statistics for this job.",
        metadata=JobMetadataConfig(complexity_hint=55),
        status="pending",
        human_review=None,
    )


def test_run_job_writes_job_metadata_after_reset_before_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_config = make_workflow_config(tmp_path)
    job = make_job()
    run_id = "20260621-102055"
    job_temp_dir = tmp_path / "temp" / "runs" / run_id / job.topic
    job_temp_dir.mkdir(parents=True)
    stale_file = job_temp_dir / "stale.txt"
    stale_file.write_text("old artifact", encoding="utf-8")
    observed_payloads: list[dict[str, Any]] = []

    def fake_run_cycle(
        _workflow_config: WorkflowConfig,
        *,
        job: JobConfig,
        job_temp_dir: Path,
        cycle_number: int,
        max_iterations_for_cycle: int,
        **_kwargs: object,
    ) -> dict[str, Any]:
        assert job.topic == "metadata-job"
        assert cycle_number == 1
        assert max_iterations_for_cycle == 5
        metadata_path = job_temp_dir / "job-metadata.json"
        assert metadata_path.is_file()
        assert not stale_file.exists()
        observed_payloads.append(json.loads(metadata_path.read_text(encoding="utf-8")))
        return {
            "next_action": "done",
            "reason": "unit-test-complete",
            "iterations": [],
            "sessions": {},
        }

    monkeypatch.setattr("agentflow_core.workflow_runner.run_cycle", fake_run_cycle)
    monkeypatch.setattr(
        "agentflow_core.workflow_runner.prompt_human_review_action",
        lambda **_kwargs: {"action": "complete_cycle"},
    )

    result = run_job(workflow_config, job=job, run_id=run_id)

    assert result["status"] == "done"
    assert observed_payloads == [
        {
            "run_id": run_id,
            "topic": "metadata-job",
            "task": "Collect statistics for this job.",
            "metadata": {"complexity_hint": 55},
            "agents": {
                "autonomy_decision": {"model": "test/model", "variant": "xhigh"},
                "execution": {"model": "test/model", "variant": "xhigh"},
                "loop_detector": {"model": "test/model", "variant": "medium"},
                "review_decision": {"model": "test/model", "variant": "xhigh"},
                "reviewer_1": {"model": "test/model", "variant": "xhigh"},
                "reviewer_2": {"model": "test/model", "variant": "xhigh"},
            },
        }
    ]
