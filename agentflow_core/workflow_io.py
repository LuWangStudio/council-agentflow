from __future__ import annotations

import shutil
import json
from pathlib import Path
from typing import Any

from agentflow_core.errors import WorkflowError


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def reset_job_temp_dir(job_temp_dir: Path) -> None:
    if job_temp_dir.exists():
        shutil.rmtree(job_temp_dir)
    job_temp_dir.mkdir(parents=True, exist_ok=True)


def load_text(path: Path) -> str:
    if not path.exists():
        raise WorkflowError(f"Expected agent output file does not exist: {path}")
    return path.read_text(encoding="utf-8")


def load_json_text_payload(path: Path, required_keys: set[str]) -> dict[str, Any]:
    text = load_text(path)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Agent output file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"Agent JSON output is not an object: {path}")
    missing = required_keys - set(payload.keys())
    if missing:
        raise WorkflowError(
            f"Agent JSON output is missing keys {sorted(missing)}: {path}"
        )
    return payload
