from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentflow_core.config import (
    DEFAULT_ATTACH_URL,
    DEFAULT_MODEL,
    DEFAULT_PROMPT_PACK,
    load_workflow_config,
    reload_job_from_jobs_file,
    update_job_status,
)
from agentflow_core.errors import ConfigError


REQUIRED_AGENT_KEYS = (
    "execution",
    "reviewer_1",
    "reviewer_2",
    "review_decision",
    "autonomy_decision",
    "loop_detector",
)


def create_prompt_pack(
    base_dir: Path,
    *,
    name: str = DEFAULT_PROMPT_PACK,
    role_prefix: str = "test",
) -> Path:
    pack_dir = base_dir / name
    pack_dir.mkdir()
    agents: dict[str, dict[str, str]] = {}
    for key in REQUIRED_AGENT_KEYS:
        prompt_file = f"{key}.md"
        agents[key] = {
            "role_name": f"{role_prefix} {key}",
            "output_name": key.replace("_", "-"),
            "prompt_file": prompt_file,
        }
        (pack_dir / prompt_file).write_text(
            f"Prompt template for {key}: ${{task}}\n",
            encoding="utf-8",
        )

    (pack_dir / "pack.yaml").write_text(
        yaml.safe_dump({"agents": agents}, sort_keys=False),
        encoding="utf-8",
    )
    return pack_dir


def base_agents() -> dict[str, dict[str, str]]:
    return {
        "execution": {},
        "reviewer_1": {},
        "reviewer_2": {},
        "review_decision": {
            "merged_review_output_path_template": "${job_temp_dir}/${agent_output_name}.review.txt",
        },
        "autonomy_decision": {
            "decision_report_output_path_template": "${job_temp_dir}/${agent_output_name}.report.md",
        },
        "loop_detector": {},
    }


def write_yaml(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def write_workflow_config(
    tmp_path: Path,
    prompt_pack_dir: Path,
    *,
    program_overrides: dict[str, Any] | None = None,
    agents: dict[str, dict[str, Any]] | None = None,
) -> Path:
    program: dict[str, Any] = {"prompt_pack_path": str(prompt_pack_dir.parent)}
    if prompt_pack_dir.name != DEFAULT_PROMPT_PACK:
        program["prompt_pack"] = prompt_pack_dir.name
    if program_overrides:
        program.update(program_overrides)
    return write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": program,
            "agents": base_agents() if agents is None else agents,
        },
    )


def write_jobs_file(tmp_path: Path, jobs: list[dict[str, Any]]) -> Path:
    return write_yaml(tmp_path / "jobs.yaml", {"jobs": jobs})


def test_loads_minimal_workflow_config_and_jobs_with_program_defaults(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "unit-tests", "task": "Add focused unit tests."}],
    )

    workflow_config = load_workflow_config(config_path, jobs_path)

    assert workflow_config.prompt_pack_dir == prompt_pack_dir.resolve()
    assert workflow_config.program.opencode_bin == "opencode"
    assert workflow_config.program.attach_url == DEFAULT_ATTACH_URL
    assert workflow_config.program.default_model == DEFAULT_MODEL
    assert workflow_config.program.default_variant is None
    assert workflow_config.program.prompt_pack == DEFAULT_PROMPT_PACK
    assert workflow_config.program.max_rounds == 1
    assert workflow_config.program.max_iterations_per_cycle == 10
    assert workflow_config.program.temp_dir == "temp"
    assert workflow_config.program.write_back is True
    assert set(workflow_config.agents) == set(REQUIRED_AGENT_KEYS)
    assert workflow_config.jobs[0].topic == "unit-tests"
    assert workflow_config.jobs[0].status == "pending"


def test_resolves_explicit_prompt_pack_path(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path, role_prefix="explicit")
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "explicit-pack", "task": "Use the explicit pack.", "status": "pending"}],
    )

    workflow_config = load_workflow_config(config_path, jobs_path)

    assert workflow_config.prompt_pack_dir == prompt_pack_dir.resolve()
    assert workflow_config.agents["execution"].role_name == "explicit execution"


def test_resolves_builtin_prompt_pack_when_no_explicit_path(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": {"prompt_pack": "implementation"},
            "agents": base_agents(),
        },
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "builtin-pack", "task": "Use built-in prompt pack."}],
    )

    workflow_config = load_workflow_config(config_path, jobs_path)

    assert workflow_config.prompt_pack_dir.name == "implementation"
    assert workflow_config.agents["execution"].prompt_template


def test_cli_prompt_pack_path_overrides_program_prompt_pack_path(
    tmp_path: Path,
) -> None:
    pack_name = "shared-pack"
    program_root = tmp_path / "program-root"
    program_root.mkdir()
    cli_root = tmp_path / "cli-root"
    cli_root.mkdir()
    program_pack_dir = create_prompt_pack(
        program_root,
        name=pack_name,
        role_prefix="program",
    )
    cli_pack_dir = create_prompt_pack(
        cli_root,
        name=pack_name,
        role_prefix="cli",
    )
    config_path = write_workflow_config(tmp_path, program_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "cli-pack", "task": "CLI pack wins.", "status": "pending"}],
    )

    workflow_config = load_workflow_config(
        config_path,
        jobs_path,
        prompt_pack_path=cli_root,
    )

    assert workflow_config.prompt_pack_dir == cli_pack_dir.resolve()
    assert workflow_config.agents["execution"].role_name == "cli execution"


@pytest.mark.parametrize(
    ("program_overrides", "expected_message"),
    [
        ({"max_rounds": 0}, "program.max_rounds"),
        ({"max_iterations_per_cycle": 0}, "program.max_iterations_per_cycle"),
        ({"temp_dir": "   "}, "program.temp_dir"),
        ({"prompt_pack": ""}, "program.prompt_pack"),
        ({"prompt_pack_path": ""}, "program.prompt_pack_path"),
        ({"default_variant": ""}, "program.default_variant"),
        ({"write_back": "true"}, "program.write_back"),
    ],
)
def test_invalid_program_config_values_raise_config_error(
    tmp_path: Path,
    program_overrides: dict[str, Any],
    expected_message: str,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(
        tmp_path,
        prompt_pack_dir,
        program_overrides=program_overrides,
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "invalid-program", "task": "Validate program config."}],
    )

    with pytest.raises(ConfigError, match=expected_message):
        load_workflow_config(config_path, jobs_path)


def test_program_section_must_be_a_mapping(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {"program": "not-a-mapping", "agents": base_agents()},
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "bad-program", "task": "Reject non-mapping program."}],
    )

    with pytest.raises(ConfigError, match="program.*must be a mapping"):
        load_workflow_config(config_path, jobs_path)


def test_valid_default_variant_is_applied_to_default_model_agents(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents["reviewer_1"] = {"variant": "reviewer-override"}
    agents["reviewer_2"] = {"model": "other/provider-model"}
    config_path = write_workflow_config(
        tmp_path,
        prompt_pack_dir,
        program_overrides={"default_variant": "default-fast"},
        agents=agents,
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "default-variant", "task": "Validate variant handling."}],
    )

    workflow_config = load_workflow_config(config_path, jobs_path)

    assert workflow_config.program.default_variant == "default-fast"
    assert workflow_config.agents["execution"].variant == "default-fast"
    assert workflow_config.agents["reviewer_1"].variant == "reviewer-override"
    assert workflow_config.agents["reviewer_2"].variant is None


def test_relative_program_prompt_pack_path_resolves_from_config_dir(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    prompt_pack_dir = create_prompt_pack(
        packs_dir,
        name="relative-pack",
        role_prefix="relative",
    )
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": {
                "prompt_pack": "relative-pack",
                "prompt_pack_path": "packs",
            },
            "agents": base_agents(),
        },
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "relative-pack", "task": "Resolve relative pack path."}],
    )

    workflow_config = load_workflow_config(config_path, jobs_path)

    assert workflow_config.prompt_pack_dir == prompt_pack_dir.resolve()
    assert workflow_config.agents["execution"].role_name == "relative execution"


def test_explicit_prompt_pack_path_requires_pack_yaml(tmp_path: Path) -> None:
    missing_packs_dir = tmp_path / "missing-packs"
    missing_packs_dir.mkdir()
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": {"prompt_pack_path": str(missing_packs_dir)},
            "agents": base_agents(),
        },
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "missing-pack-yaml", "task": "Reject missing pack.yaml."}],
    )

    with pytest.raises(ConfigError, match="missing file"):
        load_workflow_config(config_path, jobs_path)


def test_named_prompt_pack_not_found_reports_search_paths(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": {"prompt_pack": "not-a-real-pack"},
            "agents": base_agents(),
        },
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "missing-named-pack", "task": "Reject unknown prompt pack."}],
    )

    with pytest.raises(ConfigError, match="Prompt pack 'not-a-real-pack' was not found"):
        load_workflow_config(config_path, jobs_path)


def test_cli_prompt_pack_path_must_be_non_empty(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "blank-cli-pack", "task": "Reject blank CLI pack path."}],
    )

    with pytest.raises(ConfigError, match="--prompt-pack-path"):
        load_workflow_config(config_path, jobs_path, prompt_pack_path="   ")


def test_agents_section_must_be_a_mapping(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": {"prompt_pack_path": str(prompt_pack_dir.parent)},
            "agents": "not-a-mapping",
        },
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "bad-agents", "task": "Reject non-mapping agents."}],
    )

    with pytest.raises(ConfigError, match="agents.*must be a mapping"):
        load_workflow_config(config_path, jobs_path)


def test_prompt_pack_missing_required_agent_metadata_raises_config_error(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    pack_path = prompt_pack_dir / "pack.yaml"
    pack_payload = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    del pack_payload["agents"]["loop_detector"]
    write_yaml(pack_path, pack_payload)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "missing-pack-agent", "task": "Reject incomplete pack metadata."}],
    )

    with pytest.raises(ConfigError, match="missing agent metadata: agents.loop_detector"):
        load_workflow_config(config_path, jobs_path)


@pytest.mark.parametrize("field_name", ["role_name", "output_name", "prompt_file"])
def test_prompt_pack_agent_metadata_fields_must_be_non_empty_strings(
    tmp_path: Path,
    field_name: str,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    pack_path = prompt_pack_dir / "pack.yaml"
    pack_payload = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_payload["agents"]["execution"][field_name] = "   "
    write_yaml(pack_path, pack_payload)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": f"blank-pack-{field_name}", "task": "Reject blank pack metadata."}],
    )

    with pytest.raises(ConfigError, match=field_name):
        load_workflow_config(config_path, jobs_path)


@pytest.mark.parametrize(
    ("agent_overrides", "expected_message"),
    [
        ({"role_name": "   "}, "role_name"),
        ({"output_name": "   "}, "output_name"),
        ({"variant": "   "}, "variant"),
        ({"prompt_template_path": "   "}, "prompt_template_path"),
        ({"prompt_template_path": "missing-template.md"}, "does not exist"),
        ({"prompt_template": "   "}, "must provide a non-empty prompt template"),
        ({"output_path_template": "   "}, "output_path_template"),
    ],
)
def test_agent_config_invalid_fields_raise_config_error(
    tmp_path: Path,
    agent_overrides: dict[str, Any],
    expected_message: str,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents["execution"] = agent_overrides
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "invalid-agent-field", "task": "Reject invalid agent config."}],
    )

    with pytest.raises(ConfigError, match=expected_message):
        load_workflow_config(config_path, jobs_path)


def test_prompt_pack_prompt_file_must_exist(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    (prompt_pack_dir / "execution.md").unlink()
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "missing-pack-template", "task": "Reject missing prompt file."}],
    )

    with pytest.raises(ConfigError, match="Prompt template file does not exist"):
        load_workflow_config(config_path, jobs_path)


@pytest.mark.parametrize(
    ("agent_key", "template_key", "expected_message"),
    [
        (
            "review_decision",
            "merged_review_output_path_template",
            "merged_review_output_path_template",
        ),
        (
            "autonomy_decision",
            "decision_report_output_path_template",
            "decision_report_output_path_template",
        ),
    ],
)
def test_special_agent_output_templates_must_be_non_empty_when_provided(
    tmp_path: Path,
    agent_key: str,
    template_key: str,
    expected_message: str,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents[agent_key] = {template_key: "   "}
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "blank-special-template", "task": "Reject blank templates."}],
    )

    with pytest.raises(ConfigError, match=expected_message):
        load_workflow_config(config_path, jobs_path)


@pytest.mark.parametrize(
    ("config_text", "expected_message"),
    [
        ("", "Config file is empty"),
        ("- not\n- a\n- mapping\n", "Config file root must be a mapping"),
    ],
)
def test_yaml_config_file_must_be_non_empty_mapping(
    tmp_path: Path,
    config_text: str,
    expected_message: str,
) -> None:
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "bad-config-yaml", "task": "Reject malformed config YAML root."}],
    )

    with pytest.raises(ConfigError, match=expected_message):
        load_workflow_config(config_path, jobs_path)


def test_jobs_root_must_be_a_list(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_yaml(
        tmp_path / "jobs.yaml",
        {"jobs": {"topic": "not-a-list", "task": "Reject jobs mapping."}},
    )

    with pytest.raises(ConfigError, match="`jobs` must be a list"):
        load_workflow_config(config_path, jobs_path)


def test_prompt_template_path_reads_relative_template(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template_path = template_dir / "execution.md"
    template_path.write_text("Relative execution prompt: ${task}\n", encoding="utf-8")
    agents = base_agents()
    agents["execution"] = {"prompt_template_path": "templates/execution.md"}
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "relative-template", "task": "Use relative template."}],
    )

    workflow_config = load_workflow_config(config_path, jobs_path)

    assert workflow_config.agents["execution"].prompt_template == (
        "Relative execution prompt: ${task}"
    )


def test_prompt_template_path_unknown_variable_raises_config_error(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents["execution"] = {"prompt_template_path": "${unknown}/execution.md"}
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "unknown-variable", "task": "Reject unknown template variable."}],
    )

    with pytest.raises(ConfigError, match="unknown variable"):
        load_workflow_config(config_path, jobs_path)


def test_prompt_template_and_path_are_mutually_exclusive(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents["execution"] = {
        "prompt_template": "Inline prompt",
        "prompt_template_path": "execution.md",
    }
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "exclusive-template", "task": "Reject ambiguous template config."}],
    )

    with pytest.raises(ConfigError, match="cannot both be provided"):
        load_workflow_config(config_path, jobs_path)


def test_missing_required_agent_config_raises_config_error(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    del agents["reviewer_2"]
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "missing-agent", "task": "Validate agent config."}],
    )

    with pytest.raises(ConfigError, match="Missing required agent config: agents.reviewer_2"):
        load_workflow_config(config_path, jobs_path)


def test_review_decision_requires_merged_review_output_template(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents["review_decision"] = {}
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "review-template", "task": "Validate review decision config."}],
    )

    with pytest.raises(
        ConfigError,
        match="agents.review_decision.merged_review_output_path_template",
    ):
        load_workflow_config(config_path, jobs_path)


def test_autonomy_decision_requires_decision_report_output_template(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    agents = base_agents()
    agents["autonomy_decision"] = {}
    config_path = write_workflow_config(tmp_path, prompt_pack_dir, agents=agents)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "autonomy-template", "task": "Validate autonomy config."}],
    )

    with pytest.raises(
        ConfigError,
        match="agents.autonomy_decision.decision_report_output_path_template",
    ):
        load_workflow_config(config_path, jobs_path)


def test_duplicate_job_topic_raises_config_error(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [
            {"topic": "duplicate", "task": "First task."},
            {"topic": "duplicate", "task": "Second task."},
        ],
    )

    with pytest.raises(ConfigError, match="Duplicate job topic detected: duplicate"):
        load_workflow_config(config_path, jobs_path)


def test_job_human_review_must_be_non_empty_when_provided(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [
            {
                "topic": "empty-human-review",
                "task": "Validate job human review.",
                "human_review": "   ",
            }
        ],
    )

    with pytest.raises(ConfigError, match="human_review"):
        load_workflow_config(config_path, jobs_path)


def test_update_job_status_writes_back_only_status_when_enabled(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(
        tmp_path,
        prompt_pack_dir,
        program_overrides={"write_back": True},
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [
            {
                "topic": "write-back",
                "task": "Keep all other fields.",
                "status": "pending",
                "human_review": "Keep this feedback.",
            },
            {
                "topic": "untouched",
                "task": "Do not change this job.",
                "status": "pending",
            },
        ],
    )
    workflow_config = load_workflow_config(config_path, jobs_path)
    before = yaml.safe_load(jobs_path.read_text(encoding="utf-8"))
    expected = deepcopy(before)
    expected["jobs"][0]["status"] = "done"

    update_job_status(workflow_config, job_index=0, status="done")

    after = yaml.safe_load(jobs_path.read_text(encoding="utf-8"))
    assert after == expected


def test_update_job_status_skips_file_when_write_back_disabled(
    tmp_path: Path,
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(
        tmp_path,
        prompt_pack_dir,
        program_overrides={"write_back": False},
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "no-write-back", "task": "Do not rewrite.", "status": "pending"}],
    )
    workflow_config = load_workflow_config(config_path, jobs_path)
    before = jobs_path.read_text(encoding="utf-8")

    update_job_status(workflow_config, job_index=0, status="done")

    assert jobs_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "jobs_payload",
    [
        {"jobs": []},
        {"jobs": ["not-a-mapping"]},
    ],
)
def test_update_job_status_rejects_invalid_jobs_write_back_structure(
    tmp_path: Path,
    jobs_payload: dict[str, Any],
) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "invalid-write-back", "task": "Load valid config first."}],
    )
    workflow_config = load_workflow_config(config_path, jobs_path)
    write_yaml(jobs_path, jobs_payload)

    with pytest.raises(ConfigError, match="Could not write back job status"):
        update_job_status(workflow_config, job_index=0, status="done")


def test_reload_job_from_jobs_file_returns_updated_job_data(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "reload", "task": "Original task.", "status": "pending"}],
    )
    workflow_config = load_workflow_config(config_path, jobs_path)
    updated_payload = {
        "jobs": [
            {
                "topic": "reload",
                "task": "Updated task.",
                "status": "running",
                "human_review": "Fresh feedback.",
            }
        ]
    }
    write_yaml(jobs_path, updated_payload)

    job = reload_job_from_jobs_file(workflow_config, job_index=0)

    assert job.topic == "reload"
    assert job.task == "Updated task."
    assert job.status == "running"
    assert job.human_review == "Fresh feedback."


def test_reload_job_from_jobs_file_rejects_invalid_job_index(tmp_path: Path) -> None:
    prompt_pack_dir = create_prompt_pack(tmp_path)
    config_path = write_workflow_config(tmp_path, prompt_pack_dir)
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "reload-invalid", "task": "Original task."}],
    )
    workflow_config = load_workflow_config(config_path, jobs_path)

    with pytest.raises(ConfigError, match="invalid job index"):
        reload_job_from_jobs_file(workflow_config, job_index=1)


def test_load_workflow_config_rejects_missing_config_file(tmp_path: Path) -> None:
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "missing-config", "task": "Config path should be missing."}],
    )

    with pytest.raises(ConfigError, match="Config file does not exist"):
        load_workflow_config(tmp_path / "missing-workflow.yaml", jobs_path)


def test_load_workflow_config_rejects_jobs_in_common_config(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "workflow.yaml",
        {
            "program": {},
            "agents": base_agents(),
            "jobs": [{"topic": "wrong-place", "task": "Jobs do not belong here."}],
        },
    )
    jobs_path = write_jobs_file(
        tmp_path,
        [{"topic": "separate-jobs", "task": "Use separate jobs file."}],
    )

    with pytest.raises(ConfigError, match="must not contain `jobs`"):
        load_workflow_config(config_path, jobs_path)
