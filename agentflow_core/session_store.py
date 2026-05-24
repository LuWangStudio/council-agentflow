from __future__ import annotations

import json
import subprocess
from typing import Any

from agentflow_core.errors import OpencodeError


def list_sessions(opencode_bin: str) -> list[dict[str, Any]]:
    command = [opencode_bin, "session", "list", "--format", "json", "-n", "200"]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise OpencodeError(
            "Failed to list OpenCode sessions.\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    payload = json.loads(process.stdout)
    sessions: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "id" in node:
                sessions.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return sessions
