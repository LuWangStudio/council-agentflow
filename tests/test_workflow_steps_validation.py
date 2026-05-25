from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentflow_core.errors import WorkflowError
from agentflow_core.workflow_steps import (
    _load_and_validate_autonomy_decision_payload,
    _load_and_validate_loop_detector_payload,
    _load_and_validate_review_decision_payload,
)


MISSING_ADJUDICATION_MEMORY = object()


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def valid_adjudication_memory_text() -> str:
    return "# Adjudication Memory\n\n[REJECTED_OR_DEFERRED_ISSUES]\n- none\n"


def create_review_decision_artifacts(
    tmp_path: Path,
    *,
    decision_payload: dict[str, Any] | None = None,
    merged_review_text: str | None = "Merged review content.\n",
    adjudication_memory_text: str | object | None = None,
) -> tuple[Path, Path, Path]:
    decision_path = tmp_path / "review-decision.json"
    merged_review_path = tmp_path / "review-decision.review.txt"
    adjudication_memory_path = tmp_path / "adjudication-memory.latest.txt"
    write_json(
        decision_path,
        decision_payload
        or {"next_action": "done", "reason": "All acceptance items are closed."},
    )
    if merged_review_text is not None:
        merged_review_path.write_text(merged_review_text, encoding="utf-8")
    if adjudication_memory_text is not MISSING_ADJUDICATION_MEMORY:
        memory_text = (
            valid_adjudication_memory_text()
            if adjudication_memory_text is None
            else adjudication_memory_text
        )
        assert isinstance(memory_text, str)
        adjudication_memory_path.write_text(memory_text, encoding="utf-8")
    return decision_path, merged_review_path, adjudication_memory_path


@pytest.mark.parametrize(
    "next_action",
    ["rerun_execution", "human_review", "done"],
)
def test_review_decision_accepts_valid_next_action_values(
    tmp_path: Path,
    next_action: str,
) -> None:
    decision_path, merged_review_path, adjudication_memory_path = (
        create_review_decision_artifacts(
            tmp_path,
            decision_payload={
                "next_action": next_action,
                "reason": "Valid adjudicated decision.",
            },
        )
    )

    payload = _load_and_validate_review_decision_payload(
        decision_path,
        merged_review_path,
        adjudication_memory_path,
    )

    assert payload["next_action"] == next_action


def test_review_decision_rejects_invalid_next_action(tmp_path: Path) -> None:
    decision_path, merged_review_path, adjudication_memory_path = (
        create_review_decision_artifacts(
            tmp_path,
            decision_payload={"next_action": "retry", "reason": "Invalid action."},
        )
    )

    with pytest.raises(WorkflowError, match="next_action"):
        _load_and_validate_review_decision_payload(
            decision_path,
            merged_review_path,
            adjudication_memory_path,
        )


def test_review_decision_rejects_empty_reason(tmp_path: Path) -> None:
    decision_path, merged_review_path, adjudication_memory_path = (
        create_review_decision_artifacts(
            tmp_path,
            decision_payload={"next_action": "done", "reason": "   "},
        )
    )

    with pytest.raises(WorkflowError, match="reason"):
        _load_and_validate_review_decision_payload(
            decision_path,
            merged_review_path,
            adjudication_memory_path,
        )


@pytest.mark.parametrize(
    ("merged_review_text", "expected_message"),
    [
        (None, "does not exist"),
        ("   ", "Merged review output file is empty"),
    ],
)
def test_review_decision_rejects_missing_or_empty_merged_review_output(
    tmp_path: Path,
    merged_review_text: str | None,
    expected_message: str,
) -> None:
    decision_path, merged_review_path, adjudication_memory_path = (
        create_review_decision_artifacts(
            tmp_path,
            merged_review_text=merged_review_text,
        )
    )

    with pytest.raises(WorkflowError, match=expected_message):
        _load_and_validate_review_decision_payload(
            decision_path,
            merged_review_path,
            adjudication_memory_path,
        )


@pytest.mark.parametrize(
    ("adjudication_memory_text", "expected_message"),
    [
        (MISSING_ADJUDICATION_MEMORY, "does not exist"),
        ("# Wrong Title\n\n[REJECTED_OR_DEFERRED_ISSUES]\n- none\n", "must start"),
        ("# Adjudication Memory\n\nNo issue section here.\n", "must contain"),
    ],
)
def test_review_decision_rejects_missing_or_invalid_adjudication_memory(
    tmp_path: Path,
    adjudication_memory_text: str | object,
    expected_message: str,
) -> None:
    decision_path, merged_review_path, adjudication_memory_path = (
        create_review_decision_artifacts(
            tmp_path,
            adjudication_memory_text=adjudication_memory_text,
        )
    )

    with pytest.raises(WorkflowError, match=expected_message):
        _load_and_validate_review_decision_payload(
            decision_path,
            merged_review_path,
            adjudication_memory_path,
        )


def test_review_decision_accepts_extra_json_fields(tmp_path: Path) -> None:
    decision_path, merged_review_path, adjudication_memory_path = (
        create_review_decision_artifacts(
            tmp_path,
            decision_payload={
                "next_action": "done",
                "reason": "Extra fields are intentionally tolerated.",
                "extra_context": {"kept": True},
            },
        )
    )

    payload = _load_and_validate_review_decision_payload(
        decision_path,
        merged_review_path,
        adjudication_memory_path,
    )

    assert payload["extra_context"] == {"kept": True}


@pytest.mark.parametrize("next_action", ["continue", "human_review"])
def test_loop_detector_accepts_valid_next_action_values(
    tmp_path: Path,
    next_action: str,
) -> None:
    path = write_json(
        tmp_path / "loop-detector.json",
        {"next_action": next_action, "reason": "Valid loop decision."},
    )

    payload = _load_and_validate_loop_detector_payload(path)

    assert payload["next_action"] == next_action


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        ({"next_action": "stop", "reason": "Invalid action."}, "next_action"),
        ({"next_action": "continue", "reason": ""}, "reason"),
    ],
)
def test_loop_detector_rejects_invalid_next_action_and_empty_reason(
    tmp_path: Path,
    payload: dict[str, Any],
    expected_message: str,
) -> None:
    path = write_json(tmp_path / "loop-detector.json", payload)

    with pytest.raises(WorkflowError, match=expected_message):
        _load_and_validate_loop_detector_payload(path)


def test_loop_detector_accepts_extra_json_fields(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "loop-detector.json",
        {
            "next_action": "continue",
            "reason": "Extra fields are intentionally tolerated.",
            "extra_context": ["kept"],
        },
    )

    payload = _load_and_validate_loop_detector_payload(path)

    assert payload["extra_context"] == ["kept"]


def create_autonomy_decision_artifacts(
    tmp_path: Path,
    *,
    decision_payload: dict[str, Any] | None = None,
    report_text: str | None = "# Autonomy Decision Report\n\nDecision details.\n",
) -> tuple[Path, Path]:
    decision_path = tmp_path / "autonomy-decision.json"
    report_path = tmp_path / "autonomy-decision.report.md"
    write_json(
        decision_path,
        decision_payload
        or {
            "next_action": "auto_resolve",
            "reason": "Low-risk blocker can be resolved.",
            "resume_feedback": "Proceed with the documented low-risk resolution.",
        },
    )
    if report_text is not None:
        report_path.write_text(report_text, encoding="utf-8")
    return decision_path, report_path


@pytest.mark.parametrize(
    "payload",
    [
        {
            "next_action": "auto_resolve",
            "reason": "Enough evidence to resolve internally.",
            "resume_feedback": "Use this bounded resolution.",
        },
        {
            "next_action": "human_review",
            "reason": "External authorization is required.",
            "resume_feedback": "",
        },
    ],
)
def test_autonomy_decision_accepts_resume_feedback_contract(
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    decision_path, report_path = create_autonomy_decision_artifacts(
        tmp_path,
        decision_payload=payload,
    )

    loaded_payload = _load_and_validate_autonomy_decision_payload(
        decision_path,
        report_path,
    )

    assert loaded_payload == payload


def test_autonomy_decision_requires_non_empty_resume_feedback_for_auto_resolve(
    tmp_path: Path,
) -> None:
    decision_path, report_path = create_autonomy_decision_artifacts(
        tmp_path,
        decision_payload={
            "next_action": "auto_resolve",
            "reason": "Would otherwise be valid.",
            "resume_feedback": "   ",
        },
    )

    with pytest.raises(WorkflowError, match="resume_feedback"):
        _load_and_validate_autonomy_decision_payload(decision_path, report_path)


def test_autonomy_decision_requires_empty_resume_feedback_for_human_review(
    tmp_path: Path,
) -> None:
    decision_path, report_path = create_autonomy_decision_artifacts(
        tmp_path,
        decision_payload={
            "next_action": "human_review",
            "reason": "Human decision is required.",
            "resume_feedback": "Do this anyway.",
        },
    )

    with pytest.raises(WorkflowError, match="resume_feedback"):
        _load_and_validate_autonomy_decision_payload(decision_path, report_path)


def test_autonomy_decision_rejects_unsupported_extra_json_fields(
    tmp_path: Path,
) -> None:
    decision_path, report_path = create_autonomy_decision_artifacts(
        tmp_path,
        decision_payload={
            "next_action": "auto_resolve",
            "reason": "Extra fields are not supported here.",
            "resume_feedback": "Proceed.",
            "extra_context": "not allowed",
        },
    )

    with pytest.raises(WorkflowError, match="unsupported keys"):
        _load_and_validate_autonomy_decision_payload(decision_path, report_path)


@pytest.mark.parametrize(
    ("report_text", "expected_message"),
    [
        (None, "does not exist"),
        ("", "report file is empty"),
        ("# Wrong Report Title\n", "must start"),
    ],
)
def test_autonomy_decision_rejects_missing_empty_or_incorrectly_titled_report(
    tmp_path: Path,
    report_text: str | None,
    expected_message: str,
) -> None:
    decision_path, report_path = create_autonomy_decision_artifacts(
        tmp_path,
        report_text=report_text,
    )

    with pytest.raises(WorkflowError, match=expected_message):
        _load_and_validate_autonomy_decision_payload(decision_path, report_path)
