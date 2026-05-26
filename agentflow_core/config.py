from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml

from agentflow_core.errors import ConfigError


DEFAULT_MODEL = "openai/gpt-5.4"
DEFAULT_ATTACH_URL = "http://localhost:4096"
DEFAULT_PROMPT_PACK = "implementation"


@dataclass(frozen=True)
class ProgramConfig:
    opencode_bin: str
    attach_url: str
    default_model: str
    default_variant: str | None
    prompt_pack: str
    prompt_pack_path: str | None
    max_rounds: int
    max_iterations_per_cycle: int
    temp_dir: str
    write_back: bool


@dataclass(frozen=True)
class AgentConfig:
    key: str
    role_name: str
    output_name: str
    model: str
    variant: str | None
    prompt_template: str
    output_path_template: str
    merged_review_output_path_template: str | None
    decision_report_output_path_template: str | None


@dataclass(frozen=True)
class PromptPackAgentConfig:
    role_name: str
    output_name: str
    prompt_file: str


@dataclass(frozen=True)
class JobConfig:
    index: int
    topic: str
    task: str
    status: str
    human_review: str | None


@dataclass
class WorkflowConfig:
    config_path: Path
    jobs_path: Path
    prompt_pack_dir: Path
    program: ProgramConfig
    agents: dict[str, AgentConfig]
    jobs: list[JobConfig]


def _expect_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"`{field_name}` must be a mapping")
    return value


def _expect_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"`{field_name}` must be a non-empty string")
    return value.strip()


def _build_program_config(program_data: dict[str, Any]) -> ProgramConfig:
    opencode_bin = str(program_data.get("opencode_bin", "opencode"))
    attach_url = str(program_data.get("attach_url", DEFAULT_ATTACH_URL))
    default_model = str(program_data.get("default_model", DEFAULT_MODEL))
    default_variant_value = program_data.get("default_variant")
    default_variant: str | None = None
    prompt_pack_value = program_data.get("prompt_pack", DEFAULT_PROMPT_PACK)
    prompt_pack_path_value = program_data.get("prompt_pack_path")
    prompt_pack_path: str | None = None
    max_rounds = program_data.get("max_rounds", 1)
    max_iterations_per_cycle = program_data.get("max_iterations_per_cycle", 10)
    temp_dir = str(program_data.get("temp_dir", "temp"))
    write_back = program_data.get("write_back", True)

    if not isinstance(max_rounds, int) or max_rounds <= 0:
        raise ConfigError("`program.max_rounds` must be a positive integer")
    if not isinstance(max_iterations_per_cycle, int) or max_iterations_per_cycle <= 0:
        raise ConfigError(
            "`program.max_iterations_per_cycle` must be a positive integer"
        )
    if not isinstance(temp_dir, str) or not temp_dir.strip():
        raise ConfigError("`program.temp_dir` must be a non-empty string")
    if not isinstance(prompt_pack_value, str) or not prompt_pack_value.strip():
        raise ConfigError("`program.prompt_pack` must be a non-empty string")
    prompt_pack = prompt_pack_value.strip()
    if prompt_pack_path_value is not None:
        if (
            not isinstance(prompt_pack_path_value, str)
            or not prompt_pack_path_value.strip()
        ):
            raise ConfigError(
                "`program.prompt_pack_path` must be a non-empty string when provided"
            )
        prompt_pack_path = prompt_pack_path_value.strip()
    if default_variant_value is not None:
        if (
            not isinstance(default_variant_value, str)
            or not default_variant_value.strip()
        ):
            raise ConfigError("`program.default_variant` must be a non-empty string")
        default_variant = default_variant_value.strip()
    if not isinstance(write_back, bool):
        raise ConfigError("`program.write_back` must be a boolean")

    return ProgramConfig(
        opencode_bin=opencode_bin,
        attach_url=attach_url,
        default_model=default_model,
        default_variant=default_variant,
        prompt_pack=prompt_pack,
        prompt_pack_path=prompt_pack_path,
        max_rounds=max_rounds,
        max_iterations_per_cycle=max_iterations_per_cycle,
        temp_dir=temp_dir.strip(),
        write_back=write_back,
    )


def _resolve_prompt_template_path(
    prompt_template_path: str,
    *,
    agent_key: str,
    prompt_pack: str,
    prompt_pack_dir: Path,
    config_dir: Path,
) -> Path:
    try:
        rendered_path = Template(prompt_template_path).substitute(
            {
                "agent_key": agent_key,
                "prompt_pack": prompt_pack,
                "prompt_pack_dir": str(prompt_pack_dir),
            }
        )
    except KeyError as exc:
        raise ConfigError(
            f"`agents.{agent_key}.prompt_template_path` references unknown variable: {exc.args[0]}"
        ) from exc

    resolved_prompt_template_path = Path(rendered_path).expanduser()
    if not resolved_prompt_template_path.is_absolute():
        resolved_prompt_template_path = (
            config_dir / resolved_prompt_template_path
        ).resolve()
    return resolved_prompt_template_path


def _builtin_prompts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts"


def _resolve_explicit_prompt_pack_dir(
    prompt_pack_path: str,
    *,
    prompt_pack: str,
    base_dir: Path,
    field_name: str,
) -> Path:
    candidate = Path(prompt_pack_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()

    pack_yaml_path = candidate / prompt_pack / "pack.yaml"
    if not pack_yaml_path.is_file():
        raise ConfigError(
            f"`{field_name}` must point to a prompt packs directory containing "
            f"'{prompt_pack}/pack.yaml'; "
            f"missing file: {pack_yaml_path}"
        )
    return pack_yaml_path.parent


def _resolve_prompt_pack_dir(
    *,
    config_dir: Path,
    prompt_pack: str,
    explicit_prompt_pack_path: str | None,
    explicit_prompt_pack_path_base_dir: Path,
    explicit_prompt_pack_path_field_name: str,
) -> Path:
    if explicit_prompt_pack_path is not None:
        return _resolve_explicit_prompt_pack_dir(
            explicit_prompt_pack_path,
            prompt_pack=prompt_pack,
            base_dir=explicit_prompt_pack_path_base_dir,
            field_name=explicit_prompt_pack_path_field_name,
        )

    search_dirs: list[Path] = []
    for candidate in (
        (config_dir / "prompts" / prompt_pack).resolve(),
        (_builtin_prompts_dir() / prompt_pack).resolve(),
    ):
        if candidate not in search_dirs:
            search_dirs.append(candidate)
    for prompt_pack_dir in search_dirs:
        if (prompt_pack_dir / "pack.yaml").is_file():
            return prompt_pack_dir

    searched = "\n".join(
        f"- {prompt_pack_dir / 'pack.yaml'}" for prompt_pack_dir in search_dirs
    )
    raise ConfigError(
        f"Prompt pack '{prompt_pack}' was not found. Searched:\n{searched}\n"
        "Use `program.prompt_pack_path` or `--prompt-pack-path` to point to a custom prompt packs directory."
    )


def _load_prompt_pack_agents(
    *,
    prompt_pack_dir: Path,
    prompt_pack: str,
    required_agent_keys: tuple[str, ...],
) -> dict[str, PromptPackAgentConfig]:
    pack_path = prompt_pack_dir / "pack.yaml"
    pack_field_name = f"Prompt pack '{prompt_pack}' ({pack_path})"
    payload = _load_yaml_mapping(pack_path, field_name=pack_field_name)
    agents_data = _expect_mapping(
        payload.get("agents"), field_name=f"{pack_field_name} agents"
    )

    pack_agents: dict[str, PromptPackAgentConfig] = {}
    for key in required_agent_keys:
        if key not in agents_data:
            raise ConfigError(
                f"Prompt pack '{prompt_pack}' is missing agent metadata: agents.{key}"
            )
        agent_data = _expect_mapping(
            agents_data[key], field_name=f"{pack_field_name} agents.{key}"
        )
        role_name = _expect_string(
            agent_data.get("role_name"),
            field_name=f"{pack_field_name} agents.{key}.role_name",
        )
        output_name = _expect_string(
            agent_data.get("output_name"),
            field_name=f"{pack_field_name} agents.{key}.output_name",
        )
        prompt_file = _expect_string(
            agent_data.get("prompt_file"),
            field_name=f"{pack_field_name} agents.{key}.prompt_file",
        )
        pack_agents[key] = PromptPackAgentConfig(
            role_name=role_name,
            output_name=output_name,
            prompt_file=prompt_file,
        )

    return pack_agents


def _build_agent_config(
    key: str,
    data: dict[str, Any],
    *,
    default_model: str,
    default_variant: str | None,
    prompt_pack: str,
    prompt_pack_dir: Path,
    config_dir: Path,
    prompt_pack_agent: PromptPackAgentConfig,
) -> AgentConfig:
    role_name = str(data.get("role_name", prompt_pack_agent.role_name))
    output_name = str(data.get("output_name", prompt_pack_agent.output_name))
    model = str(data.get("model", default_model))
    variant_value = data.get("variant")
    variant: str | None = default_variant if model == default_model else None

    if not role_name.strip():
        raise ConfigError(f"`agents.{key}.role_name` must be a non-empty string")
    if not output_name.strip():
        raise ConfigError(f"`agents.{key}.output_name` must be a non-empty string")
    if variant_value is not None:
        if not isinstance(variant_value, str) or not variant_value.strip():
            raise ConfigError(f"`agents.{key}.variant` must be a non-empty string")
        variant = variant_value.strip()

    prompt_template = data.get("prompt_template")
    prompt_template_path = data.get("prompt_template_path")
    if prompt_template is not None and prompt_template_path is not None:
        raise ConfigError(
            f"`agents.{key}.prompt_template` and `agents.{key}.prompt_template_path` cannot both be provided"
        )

    if prompt_template_path is not None:
        if (
            not isinstance(prompt_template_path, str)
            or not prompt_template_path.strip()
        ):
            raise ConfigError(
                f"`agents.{key}.prompt_template_path` must be a non-empty string when provided"
            )
        resolved_prompt_template_path = _resolve_prompt_template_path(
            prompt_template_path.strip(),
            agent_key=key,
            prompt_pack=prompt_pack,
            prompt_pack_dir=prompt_pack_dir,
            config_dir=config_dir,
        )
        if not resolved_prompt_template_path.exists():
            raise ConfigError(
                f"Prompt template file does not exist for agents.{key}: {resolved_prompt_template_path}"
            )
        prompt_template = resolved_prompt_template_path.read_text(encoding="utf-8")
    elif prompt_template is None:
        resolved_prompt_template_path = (
            prompt_pack_dir / prompt_pack_agent.prompt_file
        ).resolve()
        if not resolved_prompt_template_path.exists():
            raise ConfigError(
                f"Prompt template file does not exist for agents.{key}: {resolved_prompt_template_path}"
            )
        prompt_template = resolved_prompt_template_path.read_text(encoding="utf-8")

    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ConfigError(
            f"`agents.{key}.prompt_template` or `agents.{key}.prompt_template_path` must provide a non-empty prompt template"
        )

    output_path_template = data.get(
        "output_path_template",
        "${job_temp_dir}/${agent_output_name}-cycle-${cycle_number}-iteration-${iteration_number}.txt",
    )
    if not isinstance(output_path_template, str) or not output_path_template.strip():
        raise ConfigError(
            f"`agents.{key}.output_path_template` must be a non-empty string"
        )

    merged_review_output_path_template = data.get("merged_review_output_path_template")
    if merged_review_output_path_template is not None:
        if (
            not isinstance(merged_review_output_path_template, str)
            or not merged_review_output_path_template.strip()
        ):
            raise ConfigError(
                f"`agents.{key}.merged_review_output_path_template` must be a non-empty string when provided"
            )
        merged_review_output_path_template = merged_review_output_path_template.strip()

    if key == "review_decision" and merged_review_output_path_template is None:
        raise ConfigError(
            "`agents.review_decision.merged_review_output_path_template` must be provided in YAML"
        )

    decision_report_output_path_template = data.get(
        "decision_report_output_path_template"
    )
    if decision_report_output_path_template is not None:
        if (
            not isinstance(decision_report_output_path_template, str)
            or not decision_report_output_path_template.strip()
        ):
            raise ConfigError(
                f"`agents.{key}.decision_report_output_path_template` must be a non-empty string when provided"
            )
        decision_report_output_path_template = (
            decision_report_output_path_template.strip()
        )

    if key == "autonomy_decision" and decision_report_output_path_template is None:
        raise ConfigError(
            "`agents.autonomy_decision.decision_report_output_path_template` must be provided in YAML"
        )

    return AgentConfig(
        key=key,
        role_name=role_name.strip(),
        output_name=output_name.strip(),
        model=model,
        variant=variant,
        prompt_template=prompt_template.strip(),
        output_path_template=output_path_template.strip(),
        merged_review_output_path_template=merged_review_output_path_template,
        decision_report_output_path_template=decision_report_output_path_template,
    )


def _build_jobs(job_items: Any) -> list[JobConfig]:
    if not isinstance(job_items, list):
        raise ConfigError("`jobs` must be a list")
    jobs: list[JobConfig] = []
    seen_topics: set[str] = set()
    for index, item in enumerate(job_items):
        job_data = _expect_mapping(item, field_name=f"jobs[{index}]")
        topic = _expect_string(job_data.get("topic"), field_name=f"jobs[{index}].topic")
        task = _expect_string(job_data.get("task"), field_name=f"jobs[{index}].task")
        status = str(job_data.get("status", "pending")).strip().lower() or "pending"
        human_review_value = job_data.get("human_review")
        human_review: str | None = None
        if human_review_value is not None:
            if (
                not isinstance(human_review_value, str)
                or not human_review_value.strip()
            ):
                raise ConfigError(
                    f"jobs[{index}].human_review must be a non-empty string when provided"
                )
            human_review = human_review_value.strip()
        if topic in seen_topics:
            raise ConfigError(f"Duplicate job topic detected: {topic}")
        seen_topics.add(topic)
        jobs.append(
            JobConfig(
                index=index,
                topic=topic,
                task=task,
                status=status,
                human_review=human_review,
            )
        )
    return jobs


def _load_yaml_mapping(path: Path, *, field_name: str) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"{field_name} file does not exist: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        raise ConfigError(f"{field_name} file is empty: {path}")
    if not isinstance(payload, dict):
        raise ConfigError(f"{field_name} file root must be a mapping: {path}")
    return payload


def load_workflow_config(
    config_path: str | Path,
    jobs_path: str | Path,
    *,
    prompt_pack_path: str | Path | None = None,
) -> WorkflowConfig:
    path = Path(config_path).expanduser().resolve()
    jobs_file_path = Path(jobs_path).expanduser().resolve()
    cli_prompt_pack_path: str | None = None
    if prompt_pack_path is not None:
        cli_prompt_pack_path = str(prompt_pack_path)
        if not cli_prompt_pack_path.strip():
            raise ConfigError("`--prompt-pack-path` must be a non-empty string")
        cli_prompt_pack_path = cli_prompt_pack_path.strip()

    payload = _load_yaml_mapping(path, field_name="Config")
    if "jobs" in payload:
        raise ConfigError(
            "Common config file must not contain `jobs`; pass jobs via --jobs instead"
        )

    jobs_payload = _load_yaml_mapping(jobs_file_path, field_name="Jobs")

    program_data = _expect_mapping(payload.get("program", {}), field_name="program")
    program = _build_program_config(program_data)
    explicit_prompt_pack_path = cli_prompt_pack_path or program.prompt_pack_path
    explicit_prompt_pack_path_base_dir = (
        Path.cwd() if cli_prompt_pack_path else path.parent
    )
    explicit_prompt_pack_path_field_name = (
        "--prompt-pack-path" if cli_prompt_pack_path else "program.prompt_pack_path"
    )
    prompt_pack_dir = _resolve_prompt_pack_dir(
        config_dir=path.parent,
        prompt_pack=program.prompt_pack,
        explicit_prompt_pack_path=explicit_prompt_pack_path,
        explicit_prompt_pack_path_base_dir=explicit_prompt_pack_path_base_dir,
        explicit_prompt_pack_path_field_name=explicit_prompt_pack_path_field_name,
    )

    agents_data = _expect_mapping(payload.get("agents"), field_name="agents")
    required_agent_keys = (
        "execution",
        "reviewer_1",
        "reviewer_2",
        "review_decision",
        "autonomy_decision",
        "loop_detector",
    )
    prompt_pack_agents = _load_prompt_pack_agents(
        prompt_pack_dir=prompt_pack_dir,
        prompt_pack=program.prompt_pack,
        required_agent_keys=required_agent_keys,
    )
    agents: dict[str, AgentConfig] = {}
    for key in required_agent_keys:
        if key not in agents_data:
            raise ConfigError(f"Missing required agent config: agents.{key}")
        agent_data = _expect_mapping(agents_data[key], field_name=f"agents.{key}")
        agents[key] = _build_agent_config(
            key,
            agent_data,
            default_model=program.default_model,
            default_variant=program.default_variant,
            prompt_pack=program.prompt_pack,
            prompt_pack_dir=prompt_pack_dir,
            config_dir=path.parent,
            prompt_pack_agent=prompt_pack_agents[key],
        )

    jobs = _build_jobs(jobs_payload.get("jobs"))
    return WorkflowConfig(
        config_path=path,
        jobs_path=jobs_file_path,
        prompt_pack_dir=prompt_pack_dir,
        program=program,
        agents=agents,
        jobs=jobs,
    )


def update_job_status(
    workflow_config: WorkflowConfig,
    *,
    job_index: int,
    status: str,
) -> None:
    if not workflow_config.program.write_back:
        return
    payload = _load_yaml_mapping(workflow_config.jobs_path, field_name="Jobs")

    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or job_index >= len(jobs):
        raise ConfigError("Could not write back job status: invalid jobs structure")

    job = jobs[job_index]
    if not isinstance(job, dict):
        raise ConfigError("Could not write back job status: job entry is not a mapping")

    job["status"] = status

    serialized = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    workflow_config.jobs_path.write_text(serialized, encoding="utf-8")


def reload_job_from_jobs_file(
    workflow_config: WorkflowConfig,
    *,
    job_index: int,
) -> JobConfig:
    payload = _load_yaml_mapping(workflow_config.jobs_path, field_name="Jobs")
    jobs = _build_jobs(payload.get("jobs"))
    if job_index >= len(jobs):
        raise ConfigError("Could not reload job from jobs file: invalid job index")
    return jobs[job_index]
