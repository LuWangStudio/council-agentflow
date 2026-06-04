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
TABLE_KEYS = ("events", *TOKEN_KEYS)
LEFT_ALIGN_KEYS = {"date", "workspace"}


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


def timestamp_to_datetime(value: float) -> datetime:
    seconds = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(seconds).astimezone()


def timestamp_to_date(value: float | None) -> str:
    if value is None:
        return "unknown"
    return timestamp_to_datetime(value).strftime("%Y-%m-%d")


def parse_since(value: str) -> float:
    stripped = value.strip()
    try:
        return datetime.strptime(stripped, "%Y-%m-%d").astimezone().timestamp() * 1000
    except ValueError:
        pass

    parts = stripped.split()
    if len(parts) == 2 and parts[1] == "days" and parts[0].isdigit():
        return (datetime.now().astimezone() - timedelta(days=int(parts[0]))).timestamp() * 1000

    raise ValueError("invalid --since")


def format_timestamp(value: float | None) -> str:
    if value is None:
        return ""
    return timestamp_to_datetime(value).strftime("%Y-%m-%d %H:%M:%S%z")


def format_value(key: str, value: Any) -> str:
    if key == "cost":
        return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def display_workspace(workspace: str) -> str:
    if workspace == "unknown":
        return workspace
    name = Path(workspace).name
    return name or workspace


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


def summarize(
    database: Path, *, workspace: str | None, since_timestamp: float | None
) -> dict[str, Any]:
    database = database.expanduser().resolve()
    workspace_filter = None if workspace is None else normalize_workspace(workspace)

    totals: Counter[str] = Counter()
    by_day: defaultdict[str, Counter[str]] = defaultdict(Counter)
    by_day_workspace: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    day_events: Counter[str] = Counter()
    day_workspace_events: Counter[tuple[str, str]] = Counter()
    workspaces_by_day: defaultdict[str, set[str]] = defaultdict(set)
    events = 0

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

        timestamp = message_timestamp(
            message,
            fallback_created=row["message_time_created"],
            fallback_updated=row["message_time_updated"],
        )
        if since_timestamp is not None and (
            timestamp is None or timestamp < since_timestamp
        ):
            continue

        day = timestamp_to_date(timestamp)
        day_workspace = (day, workspace_name)
        events += 1
        totals.update(counters)
        by_day[day].update(counters)
        by_day_workspace[day_workspace].update(counters)
        day_events[day] += 1
        day_workspace_events[day_workspace] += 1
        workspaces_by_day[day].add(workspace_name)

    ordered_days = sorted(by_day)
    return {
        "database": str(database),
        "workspace_filter": workspace_filter,
        "since_timestamp": since_timestamp,
        "since_time": format_timestamp(since_timestamp),
        "days": len(ordered_days),
        "events": events,
        "totals": {key: totals[key] for key in TOKEN_KEYS},
        "by_day": {
            day: {
                "events": day_events[day],
                **{key: by_day[day][key] for key in TOKEN_KEYS},
                "by_workspace": {
                    workspace_name: {
                        "events": day_workspace_events[(day, workspace_name)],
                        **{
                            key: by_day_workspace[(day, workspace_name)][key]
                            for key in TOKEN_KEYS
                        },
                    }
                    for workspace_name in sorted(workspaces_by_day[day])
                },
            }
            for day in ordered_days
        },
    }


def table_rows(summary: dict[str, Any], *, include_day_total: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    for day, day_summary in summary["by_day"].items():
        for workspace_name, workspace_summary in day_summary["by_workspace"].items():
            rows.append(
                [
                    day,
                    display_workspace(workspace_name),
                    *[format_value(key, workspace_summary[key]) for key in TABLE_KEYS],
                ]
            )
        if include_day_total:
            rows.append(
                [
                    day,
                    "DAY-TOTAL",
                    *[format_value(key, day_summary[key]) for key in TABLE_KEYS],
                ]
            )
    rows.append(
        [
            "GRAND TOTAL",
            "",
            str(summary["events"]),
            *[format_value(key, summary["totals"][key]) for key in TOKEN_KEYS],
        ]
    )
    return rows


def print_text_summary(summary: dict[str, Any]) -> None:
    print(f"database: {summary['database']}")
    if summary["workspace_filter"] is not None:
        print(f"workspace_filter: {summary['workspace_filter']}")
    if summary["since_timestamp"] is not None:
        print(f"since_time: {summary['since_time']}")
    print(f"days: {summary['days']}")
    print(f"events: {summary['events']}")

    headers = ["date", "workspace", *TABLE_KEYS]
    table = [
        headers,
        *table_rows(
            summary,
            include_day_total=summary["workspace_filter"] is None,
        ),
    ]
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
        description="Summarize daily token usage from OpenCode SQLite storage."
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
        print_text_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
