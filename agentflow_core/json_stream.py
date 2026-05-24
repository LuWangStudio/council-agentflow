from __future__ import annotations

import json
from typing import Any


def parse_event_stream(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def extract_session_id(events: list[dict[str, Any]]) -> str | None:
    candidate_keys = {"sessionID", "sessionId", "session_id"}
    for event in reversed(events):
        stack = [event]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in candidate_keys and isinstance(value, str):
                        return value
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    return None
