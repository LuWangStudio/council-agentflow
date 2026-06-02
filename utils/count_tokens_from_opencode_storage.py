#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


TOKEN_KEYS = (
    "total",
    "input",
    "output",
    "reasoning",
    "cache_write",
    "cache_read",
    "cost",
)
TABLE_KEYS = ("first_time", "last_time", "events", *TOKEN_KEYS)
LEFT_ALIGN_KEYS = {"workspace", "session", "first_time", "last_time"}


def normalize_workspace(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def as_number(value: Any) -> int | float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return value
    return 0


def as_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_timestamp(value: float | None) -> str:
    if value is None:
        return ""
    seconds = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(seconds).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def parse_since(value: str) -> float:
    stripped = value.strip()
    try:
        return datetime.strptime(stripped, "%Y-%m-%d").timestamp() * 1000
    except ValueError:
        pass

    parts = stripped.split()
    if len(parts) == 2 and parts[1] == "days" and parts[0].isdigit():
        return (datetime.now().astimezone() - timedelta(days=int(parts[0]))).timestamp() * 1000

    raise ValueError("invalid --since")


def format_value(key: str, value: Any) -> str:
    if key == "cost":
        return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def display_workspace(workspace: str) -> str:
    if workspace == "unknown":
        return workspace
    name = Path(workspace).name
    return name or workspace


def display_session(session_id: str) -> str:
    if session_id == "WORKSPACE TOTAL":
        return "WS-TOTAL"
    if not session_id:
        return session_id
    if len(session_id) <= 8:
        return session_id
    return f"{session_id[:3]}..{session_id[-3:]}"


def parse_message_data(raw_data: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def message_timestamp(
    message: dict[str, Any], *, fallback_created: Any, fallback_updated: Any
) -> float | None:
    time_value = message.get("time")
    if isinstance(time_value, dict):
        timestamp = as_timestamp(time_value.get("completed") or time_value.get("created"))
        if timestamp is not None:
            return timestamp
    return as_timestamp(fallback_updated or fallback_created)


def token_counters(message: dict[str, Any]) -> Counter[str] | None:
    tokens = message.get("tokens")
    if not isinstance(tokens, dict):
        return None

    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        cache = {}

    input_tokens = as_number(tokens.get("input"))
    output_tokens = as_number(tokens.get("output"))
    reasoning_tokens = as_number(tokens.get("reasoning"))
    cache_write = as_number(cache.get("write"))
    cache_read = as_number(cache.get("read"))
    total = as_number(tokens.get("total")) or (
        input_tokens + output_tokens + reasoning_tokens + cache_write + cache_read
    )

    return Counter(
        {
            "total": total,
            "input": input_tokens,
            "output": output_tokens,
            "reasoning": reasoning_tokens,
            "cache_write": cache_write,
            "cache_read": cache_read,
            "cost": as_number(message.get("cost")),
        }
    )


def opencode_rows(database: Path) -> list[sqlite3.Row]:
    query = """
        SELECT
            message.id AS message_id,
            message.session_id AS session_id,
            message.time_created AS message_time_created,
            message.time_updated AS message_time_updated,
            message.data AS message_data,
            session.directory AS session_directory,
            session.time_created AS session_time_created,
            session.time_updated AS session_time_updated,
            session.title AS session_title,
            workspace.directory AS workspace_directory,
            project.worktree AS project_worktree
        FROM message
        JOIN session ON session.id = message.session_id
        LEFT JOIN workspace ON workspace.id = session.workspace_id
        LEFT JOIN project ON project.id = session.project_id
        WHERE message.data LIKE '%"tokens"%'
        ORDER BY message.time_created ASC, message.id ASC
    """
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(query))


def row_workspace(row: sqlite3.Row) -> str:
    for key in ("session_directory", "workspace_directory", "project_worktree"):
        value = row[key]
        if isinstance(value, str) and value.strip():
            return normalize_workspace(value)
    return "unknown"


def update_timestamp_range(
    first_timestamps: dict[str, float], last_timestamps: dict[str, float], key: str, timestamp: float | None
) -> None:
    if timestamp is None:
        return
    first_timestamps[key] = min(timestamp, first_timestamps.get(key, timestamp))
    last_timestamps[key] = max(timestamp, last_timestamps.get(key, timestamp))


def summarize(
    database: Path, *, workspace: str | None, since_timestamp: float | None
) -> dict[str, Any]:
    database = database.expanduser().resolve()
    workspace_filter = None if workspace is None else normalize_workspace(workspace)

    session_totals: defaultdict[str, Counter[str]] = defaultdict(Counter)
    session_events: Counter[str] = Counter()
    session_first_timestamps: dict[str, float] = {}
    session_last_timestamps: dict[str, float] = {}
    session_workspace: dict[str, str] = {}

    for row in opencode_rows(database):
        workspace_name = row_workspace(row)
        if workspace_filter is not None and workspace_name != workspace_filter:
            continue

        message = parse_message_data(str(row["message_data"]))
        if message is None:
            continue

        counters = token_counters(message)
        if counters is None:
            continue

        session_id = str(row["session_id"])
        timestamp = message_timestamp(
            message,
            fallback_created=row["message_time_created"],
            fallback_updated=row["message_time_updated"],
        )

        session_totals[session_id].update(counters)
        session_events[session_id] += 1
        session_workspace[session_id] = workspace_name
        update_timestamp_range(
            session_first_timestamps,
            session_last_timestamps,
            session_id,
            timestamp,
        )

    included_session_ids = [
        session_id
        for session_id in session_totals
        if since_timestamp is None
        or session_last_timestamps.get(session_id, float("-inf")) >= since_timestamp
    ]

    totals: Counter[str] = Counter()
    workspace_totals: defaultdict[str, Counter[str]] = defaultdict(Counter)
    workspace_events: Counter[str] = Counter()
    workspace_sessions: defaultdict[str, set[str]] = defaultdict(set)
    workspace_first_timestamps: dict[str, float] = {}
    workspace_last_timestamps: dict[str, float] = {}
    events = 0

    for session_id in included_session_ids:
        workspace_name = session_workspace[session_id]
        totals.update(session_totals[session_id])
        workspace_totals[workspace_name].update(session_totals[session_id])
        events += session_events[session_id]
        workspace_events[workspace_name] += session_events[session_id]
        workspace_sessions[workspace_name].add(session_id)

        first_timestamp = session_first_timestamps.get(session_id)
        if first_timestamp is not None:
            workspace_first_timestamps[workspace_name] = min(
                first_timestamp,
                workspace_first_timestamps.get(workspace_name, first_timestamp),
            )
        last_timestamp = session_last_timestamps.get(session_id)
        if last_timestamp is not None:
            workspace_last_timestamps[workspace_name] = max(
                last_timestamp,
                workspace_last_timestamps.get(workspace_name, last_timestamp),
            )

    ordered_workspaces = sorted(
        workspace_totals,
        key=lambda name: (workspace_last_timestamps.get(name, float("inf")), name),
    )
    first_timestamp = min(workspace_first_timestamps.values(), default=None)
    last_timestamp = max(workspace_last_timestamps.values(), default=None)

    by_workspace: dict[str, Any] = {}
    for workspace_name in ordered_workspaces:
        session_ids = sorted(
            workspace_sessions[workspace_name],
            key=lambda session_id: (
                session_last_timestamps.get(session_id, float("inf")),
                session_id,
            ),
        )
        by_workspace[workspace_name] = {
            "first_timestamp": workspace_first_timestamps.get(workspace_name),
            "first_time": format_timestamp(workspace_first_timestamps.get(workspace_name)),
            "last_timestamp": workspace_last_timestamps.get(workspace_name),
            "last_time": format_timestamp(workspace_last_timestamps.get(workspace_name)),
            "sessions": len(workspace_sessions[workspace_name]),
            "events": workspace_events[workspace_name],
            **{key: workspace_totals[workspace_name][key] for key in TOKEN_KEYS},
            "by_session": {
                session_id: {
                    "first_timestamp": session_first_timestamps.get(session_id),
                    "first_time": format_timestamp(session_first_timestamps.get(session_id)),
                    "last_timestamp": session_last_timestamps.get(session_id),
                    "last_time": format_timestamp(session_last_timestamps.get(session_id)),
                    "events": session_events[session_id],
                    **{key: session_totals[session_id][key] for key in TOKEN_KEYS},
                }
                for session_id in session_ids
                if session_workspace.get(session_id) == workspace_name
            },
        }

    return {
        "database": str(database),
        "workspace_filter": workspace_filter,
        "since_timestamp": since_timestamp,
        "since_time": format_timestamp(since_timestamp),
        "workspaces": len(by_workspace),
        "sessions": sum(len(sessions) for sessions in workspace_sessions.values()),
        "events": events,
        "first_timestamp": first_timestamp,
        "first_time": format_timestamp(first_timestamp),
        "last_timestamp": last_timestamp,
        "last_time": format_timestamp(last_timestamp),
        "totals": {key: totals[key] for key in TOKEN_KEYS},
        "by_workspace": by_workspace,
    }


def table_rows(summary: dict[str, Any], *, total_only: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    for workspace_name, workspace_summary in summary["by_workspace"].items():
        if not total_only:
            for session_id, session_summary in workspace_summary["by_session"].items():
                rows.append(
                    [
                        display_workspace(workspace_name),
                        display_session(session_id),
                        *[format_value(key, session_summary[key]) for key in TABLE_KEYS],
                    ]
                )
        rows.append(
            [
                display_workspace(workspace_name),
                display_session("WORKSPACE TOTAL"),
                *[format_value(key, workspace_summary[key]) for key in TABLE_KEYS],
            ]
        )
    rows.append(
        [
            "GRAND TOTAL",
            "",
            str(summary["first_time"]),
            str(summary["last_time"]),
            str(summary["events"]),
            *[format_value(key, summary["totals"][key]) for key in TOKEN_KEYS],
        ]
    )
    return rows


def print_text_summary(summary: dict[str, Any], *, total_only: bool) -> None:
    print(f"database: {summary['database']}")
    if summary["workspace_filter"] is not None:
        print(f"workspace_filter: {summary['workspace_filter']}")
    if summary["since_timestamp"] is not None:
        print(f"since_time: {summary['since_time']}")
    print(f"workspaces: {summary['workspaces']}")
    print(f"sessions: {summary['sessions']}")
    print(f"events: {summary['events']}")

    headers = ["workspace", "session", *TABLE_KEYS]
    table = [headers, *table_rows(summary, total_only=total_only)]
    widths = [max(len(row[index]) for row in table) for index in range(len(headers))]

    print()
    for row_index, row in enumerate(table):
        formatted_cells = []
        for index, cell in enumerate(row):
            key = headers[index]
            if key in LEFT_ALIGN_KEYS:
                formatted_cells.append(cell.ljust(widths[index]))
            else:
                formatted_cells.append(cell.rjust(widths[index]))
        print("  ".join(formatted_cells))
        if row_index == 0:
            print("  ".join("-" * width for width in widths))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize token usage from OpenCode SQLite storage."
    )
    parser.add_argument(
        "--workspace",
        help="Workspace path to include. If omitted, all workspaces are included.",
    )
    parser.add_argument(
        "--since",
        help="Filter messages since YYYY-MM-DD or '<N> days'.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        help="OpenCode SQLite database path. Defaults to ~/.local/share/opencode/opencode.db.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON instead of text.",
    )
    parser.add_argument(
        "--total-only",
        action="store_true",
        help="In text output, show only each workspace total plus the grand total.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database.is_file():
        print(f"OpenCode SQLite database does not exist: {args.database}", file=sys.stderr)
        return 1

    since_timestamp = None
    if args.since is not None:
        try:
            since_timestamp = parse_since(args.since)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    summary = summarize(
        args.database,
        workspace=args.workspace,
        since_timestamp=since_timestamp,
    )
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_text_summary(summary, total_only=args.total_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
