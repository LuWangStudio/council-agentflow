#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_MODEL = "openai/gpt-5.4"
METADATA_FILENAME = "job-metadata.json"
TASK_NOT_FOUND = "not found"


@dataclass(frozen=True)
class JobRun:
    run_id: str
    topic: str
    path: Path


@dataclass(frozen=True)
class WorkflowConfigSource:
    path: Path
    agents: dict[str, dict[str, str | None]]
    temp_dir: str | None
    score: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively generate job-metadata.json files for existing "
            "Council-Agentflow run job directories."
        )
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        required=True,
        help="Agentflow runs directory, e.g. .agentflow-temp/runs.",
    )
    return parser.parse_args()


def load_yaml_mapping(path: Path) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return payload if isinstance(payload, dict) else None


def load_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def workspace_root_for_runs_dir(runs_dir: Path) -> Path:
    # Expected shape: <workspace>/<agentflow-temp-dir>/runs.
    # With only --runs-dir available, this is the best local source for YAML files.
    return runs_dir.parent.parent.resolve()


def top_level_yaml_files(root: Path) -> list[Path]:
    return sorted([*root.glob("*.yaml"), *root.glob("*.yml")])


def discover_job_runs(runs_dir: Path) -> list[JobRun]:
    job_runs: list[JobRun] = []
    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        for job_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
            job_runs.append(
                JobRun(run_id=run_dir.name, topic=job_dir.name, path=job_dir)
            )
    return job_runs


def parse_workflow_config_source(
    path: Path,
    *,
    runs_dir: Path,
    root: Path,
) -> WorkflowConfigSource | None:
    payload = load_yaml_mapping(path)
    if payload is None:
        return None

    program = payload.get("program")
    agents_data = payload.get("agents")
    if not isinstance(program, dict) or not isinstance(agents_data, dict):
        return None
    if "jobs" in payload:
        return None

    default_model = str(program.get("default_model", DEFAULT_MODEL))
    default_variant_value = program.get("default_variant")
    default_variant = (
        default_variant_value.strip()
        if isinstance(default_variant_value, str) and default_variant_value.strip()
        else None
    )
    temp_dir_value = program.get("temp_dir")
    temp_dir = (
        temp_dir_value.strip()
        if isinstance(temp_dir_value, str) and temp_dir_value.strip()
        else None
    )

    agents: dict[str, dict[str, str | None]] = {}
    for key, value in sorted(agents_data.items()):
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        model = str(value.get("model", default_model))
        variant_value = value.get("variant")
        variant = default_variant if model == default_model else None
        if isinstance(variant_value, str) and variant_value.strip():
            variant = variant_value.strip()
        agents[key] = {"model": model, "variant": variant}

    score = 0
    if temp_dir is not None and (root / temp_dir).resolve() == runs_dir.parent.resolve():
        score += 20
    lowered_name = path.name.lower()
    if "config" in lowered_name:
        score += 5
    if "workflow" in lowered_name:
        score += 5

    return WorkflowConfigSource(
        path=path,
        agents=agents,
        temp_dir=temp_dir,
        score=score,
    )


def discover_workflow_config_sources(
    root: Path, *, runs_dir: Path
) -> list[WorkflowConfigSource]:
    sources = []
    for path in top_level_yaml_files(root):
        source = parse_workflow_config_source(
            path,
            runs_dir=runs_dir,
            root=root,
        )
        if source is not None:
            sources.append(source)
    return sorted(sources, key=lambda source: (source.score, source.path.name), reverse=True)


def choose_workflow_config_source(
    sources: list[WorkflowConfigSource],
) -> WorkflowConfigSource | None:
    if not sources:
        return None
    best_score = sources[0].score
    best_sources = [source for source in sources if source.score == best_score]
    if len(best_sources) == 1:
        return best_sources[0]

    print("Multiple workflow config YAML files are plausible:")
    for index, source in enumerate(best_sources, start=1):
        print(f"  {index}. {source.path} (score={source.score})")
    selected = input("Select workflow config file [1]: ").strip()
    if selected.isdigit() and 1 <= int(selected) <= len(best_sources):
        return best_sources[int(selected) - 1]
    return best_sources[0]


def parse_complexity_hint(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 100 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if 1 <= parsed <= 100 else None
    return None


def existing_complexity_hint(metadata_path: Path) -> int | None:
    existing = load_json_mapping(metadata_path)
    metadata = existing.get("metadata") if existing is not None else None
    if not isinstance(metadata, dict):
        return None
    return parse_complexity_hint(metadata.get("complexity_hint"))


def prompt_complexity_hint(job_run: JobRun, task: str, default: int | None) -> int:
    print("\n" + "=" * 80)
    print(f"run_id: {job_run.run_id}")
    print(f"topic: {job_run.topic}")
    print("task:")
    print(task or "<task not found>")
    print("-" * 80)

    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"complexity_hint{suffix}: ").strip()
        if not value and default is not None:
            return default
        parsed = parse_complexity_hint(value)
        if parsed is not None:
            return parsed
        print("complexity_hint must be an integer from 1 to 100.")


def write_job_metadata(
    job_run: JobRun,
    *,
    task: str,
    complexity_hint: int,
    agents: dict[str, dict[str, str | None]],
) -> Path:
    payload = {
        "run_id": job_run.run_id,
        "topic": job_run.topic,
        "task": task,
        "metadata": {"complexity_hint": complexity_hint},
        "agents": agents,
    }
    metadata_path = job_run.path / METADATA_FILENAME
    metadata_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return metadata_path


def main() -> int:
    args = parse_args()
    runs_dir = args.runs_dir.expanduser().resolve()
    if not runs_dir.is_dir():
        print(f"Agentflow runs directory does not exist: {runs_dir}", file=sys.stderr)
        return 1

    job_runs = discover_job_runs(runs_dir)
    if not job_runs:
        print(f"No run job directories found under: {runs_dir}", file=sys.stderr)
        return 1

    root = workspace_root_for_runs_dir(runs_dir)

    config_source = choose_workflow_config_source(
        discover_workflow_config_sources(
            root,
            runs_dir=runs_dir,
        )
    )
    if config_source is not None:
        print(f"Using workflow config file: {config_source.path}")
        agents = config_source.agents
    else:
        print(
            "Warning: no workflow config YAML file found; using an empty agents map.",
            file=sys.stderr,
        )
        agents = {}

    complexity_cache: dict[tuple[str, str], int] = {}
    written_paths: list[Path] = []
    for job_run in job_runs:
        metadata_path = job_run.path / METADATA_FILENAME
        task = TASK_NOT_FOUND
        default_hint = existing_complexity_hint(metadata_path) or complexity_cache.get(
            (job_run.topic, task)
        )
        complexity_hint = prompt_complexity_hint(job_run, task, default_hint)
        complexity_cache[(job_run.topic, task)] = complexity_hint
        written_paths.append(
            write_job_metadata(
                job_run,
                task=task,
                complexity_hint=complexity_hint,
                agents=agents,
            )
        )
        print(f"Wrote: {written_paths[-1]}")

    print(f"\nGenerated {len(written_paths)} job metadata file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
