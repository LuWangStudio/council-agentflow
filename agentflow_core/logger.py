from __future__ import annotations

from datetime import datetime
import sys


_QUIET_MODE = False
_VERBOSE_MODE = False


def configure_logging(*, quiet: bool, verbose: bool) -> None:
    global _QUIET_MODE, _VERBOSE_MODE
    _QUIET_MODE = quiet
    _VERBOSE_MODE = verbose


def log_step(message: str) -> None:
    if _QUIET_MODE:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def log_verbose(title: str, content: str) -> None:
    if _QUIET_MODE or not _VERBOSE_MODE:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {title}", flush=True)
    print(content, flush=True)


def log_raw_event(line: str) -> None:
    if _QUIET_MODE or not _VERBOSE_MODE:
        return
    prefix = "OC_EVENT: "
    if sys.stderr.isatty():
        print(f"\033[36m{prefix}{line}\033[0m", file=sys.stderr, flush=True)
        return
    print(f"{prefix}{line}", file=sys.stderr, flush=True)
