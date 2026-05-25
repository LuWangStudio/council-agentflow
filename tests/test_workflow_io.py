from __future__ import annotations

from pathlib import Path

import pytest

from agentflow_core.errors import WorkflowError
from agentflow_core.workflow_io import (
    load_json_text_payload,
    load_text,
    read_text_if_exists,
    reset_job_temp_dir,
)


def test_load_text_raises_workflow_error_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkflowError, match="Expected agent output file does not exist"):
        load_text(tmp_path / "missing.txt")


def test_load_text_returns_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "output.txt"
    path.write_text("agent output", encoding="utf-8")

    assert load_text(path) == "agent output"


def test_read_text_if_exists_returns_empty_string_for_missing_file(
    tmp_path: Path,
) -> None:
    assert read_text_if_exists(tmp_path / "missing.txt") == ""


def test_read_text_if_exists_returns_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "artifact.txt"
    path.write_text("artifact content", encoding="utf-8")

    assert read_text_if_exists(path) == "artifact content"


def test_load_json_text_payload_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(WorkflowError, match="not valid JSON"):
        load_json_text_payload(path, {"next_action"})


def test_load_json_text_payload_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text('["not", "an", "object"]', encoding="utf-8")

    with pytest.raises(WorkflowError, match="JSON output is not an object"):
        load_json_text_payload(path, {"next_action"})


def test_load_json_text_payload_rejects_missing_required_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "payload.json"
    path.write_text('{"next_action": "done"}', encoding="utf-8")

    with pytest.raises(WorkflowError, match="missing keys"):
        load_json_text_payload(path, {"next_action", "reason"})


def test_load_json_text_payload_accepts_valid_json_object(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text(
        '{"next_action": "done", "reason": "complete", "extra": true}',
        encoding="utf-8",
    )

    payload = load_json_text_payload(path, {"next_action", "reason"})

    assert payload == {"next_action": "done", "reason": "complete", "extra": True}


def test_reset_job_temp_dir_clears_existing_directory_and_recreates_it(
    tmp_path: Path,
) -> None:
    job_temp_dir = tmp_path / "job-temp"
    nested_dir = job_temp_dir / "nested"
    nested_dir.mkdir(parents=True)
    old_file = nested_dir / "old.txt"
    old_file.write_text("stale artifact", encoding="utf-8")

    reset_job_temp_dir(job_temp_dir)

    assert job_temp_dir.is_dir()
    assert not old_file.exists()
    assert list(job_temp_dir.iterdir()) == []


def test_reset_job_temp_dir_creates_missing_directory(tmp_path: Path) -> None:
    job_temp_dir = tmp_path / "new" / "job-temp"

    reset_job_temp_dir(job_temp_dir)

    assert job_temp_dir.is_dir()
    assert list(job_temp_dir.iterdir()) == []
