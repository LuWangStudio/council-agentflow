#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime
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
LEFT_ALIGN_KEYS = {"session", "first_time", "last_time"}


def expand_log_paths(paths: Iterable[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_file():
            expanded.append(path)
        elif path.is_dir():
            expanded.extend(sorted(child for child in path.rglob("*") if child.is_file()))
    return expanded


def iter_lines(paths: Iterable[Path]) -> Iterator[str]:
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as file:
            yield from file


def parse_oc_event(line: str) -> dict[str, Any] | None:
    if "OC_EVENT:" not in line:
        return None
    raw = line.split("OC_EVENT:", 1)[1].strip()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    return event


def parse_timestamp(event: dict[str, Any]) -> float | None:
    value = event.get("timestamp")
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


def token_row(event: dict[str, Any]) -> tuple[str, str, float | None, Counter[str]] | None:
    if event.get("type") != "step_finish":
        return None

    part = event.get("part")
    if not isinstance(part, dict):
        return None

    tokens = part.get("tokens")
    if not isinstance(tokens, dict):
        return None

    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        cache = {}

    session_id = str(event.get("sessionID") or part.get("sessionID") or "unknown")
    part_id = str(part.get("id") or "")
    timestamp = parse_timestamp(event)

    return (
        session_id,
        part_id,
        timestamp,
        Counter(
            {
                "total": tokens.get("total", 0) or 0,
                "input": tokens.get("input", 0) or 0,
                "output": tokens.get("output", 0) or 0,
                "reasoning": tokens.get("reasoning", 0) or 0,
                "cache_write": cache.get("write", 0) or 0,
                "cache_read": cache.get("read", 0) or 0,
                "cost": part.get("cost", 0) or 0,
            }
        ),
    )


def summarize(paths: Iterable[Path], *, dedupe: bool) -> dict[str, Any]:
    log_paths = expand_log_paths(paths)
    totals: Counter[str] = Counter()
    by_session: defaultdict[str, Counter[str]] = defaultdict(Counter)
    by_session_events: Counter[str] = Counter()
    first_timestamps: dict[str, float] = {}
    last_timestamps: dict[str, float] = {}
    seen_part_ids: set[str] = set()
    events = 0
    skipped_duplicates = 0

    for line in iter_lines(log_paths):
        event = parse_oc_event(line)
        if event is None:
            continue

        row = token_row(event)
        if row is None:
            continue

        session_id, part_id, timestamp, counters = row
        if dedupe and part_id:
            if part_id in seen_part_ids:
                skipped_duplicates += 1
                continue
            seen_part_ids.add(part_id)

        events += 1
        totals.update(counters)
        by_session[session_id].update(counters)
        by_session_events[session_id] += 1
        if timestamp is not None:
            first_timestamps[session_id] = min(
                timestamp, first_timestamps.get(session_id, timestamp)
            )
            last_timestamps[session_id] = max(
                timestamp, last_timestamps.get(session_id, timestamp)
            )

    ordered_sessions = sorted(
        by_session.items(),
        key=lambda item: (first_timestamps.get(item[0], float("inf")), item[0]),
    )
    first_timestamp = min(first_timestamps.values(), default=None)
    last_timestamp = max(last_timestamps.values(), default=None)

    return {
        "files": len(log_paths),
        "events": events,
        "skipped_duplicates": skipped_duplicates,
        "first_timestamp": first_timestamp,
        "first_time": format_timestamp(first_timestamp),
        "last_timestamp": last_timestamp,
        "last_time": format_timestamp(last_timestamp),
        "totals": {key: totals[key] for key in TOKEN_KEYS},
        "by_session": {
            session_id: {
                "first_timestamp": first_timestamps.get(session_id),
                "first_time": format_timestamp(first_timestamps.get(session_id)),
                "last_timestamp": last_timestamps.get(session_id),
                "last_time": format_timestamp(last_timestamps.get(session_id)),
                "events": by_session_events[session_id],
                **{key: counters[key] for key in TOKEN_KEYS},
            }
            for session_id, counters in ordered_sessions
        },
    }


def format_value(key: str, value: Any) -> str:
    if key == "cost":
        return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def print_text_summary(summary: dict[str, Any]) -> None:
    print(f"files: {summary['files']}")
    print(f"events: {summary['events']}")
    print(f"skipped_duplicates: {summary['skipped_duplicates']}")

    rows = [
        [session_id, *[format_value(key, counters[key]) for key in TABLE_KEYS]]
        for session_id, counters in summary["by_session"].items()
    ]
    rows.append(
        [
            "GRAND TOTAL",
            str(summary["first_time"]),
            str(summary["last_time"]),
            str(summary["events"]),
            *[format_value(key, summary["totals"][key]) for key in TOKEN_KEYS],
        ]
    )

    headers = ["session", *TABLE_KEYS]
    table = [headers, *rows]
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
        description="Summarize token usage from council-agentflow log OC_EVENT entries."
    )
    parser.add_argument(
        "logs",
        nargs="+",
        type=Path,
        help="Log file or directory path(s) to parse. Directories are scanned recursively.",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Do not deduplicate repeated step_finish events by part.id.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON instead of text.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing_paths = [path for path in args.logs if not path.exists()]
    if missing_paths:
        for path in missing_paths:
            print(f"Log path does not exist: {path}", file=sys.stderr)
        return 1

    invalid_paths = [path for path in args.logs if not path.is_file() and not path.is_dir()]
    if invalid_paths:
        for path in invalid_paths:
            print(f"Log path is not a file or directory: {path}", file=sys.stderr)
        return 1

    summary = summarize(args.logs, dedupe=not args.no_dedupe)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_text_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
