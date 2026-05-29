# Configuration reference

`council-agentflow` uses two YAML files:

- a shared workflow config passed with `--config`, containing `program` and `agents`;
- a jobs file passed with `--jobs`, containing `jobs`.

The common config must not contain `jobs`.

## Program config

Code defaults when a field is omitted from `program`:

| Field | Default |
| --- | --- |
| `opencode_bin` | `opencode` |
| `attach_url` | `http://localhost:4096` |
| `default_model` | `openai/gpt-5.4` |
| `default_variant` | unset; no `--variant` is passed unless an agent sets one |
| `prompt_pack` | `implementation` |
| `prompt_pack_path` | unset; use prompt-pack resolution order below |
| `max_rounds` | `1` |
| `max_iterations_per_cycle` | `10` |
| `temp_dir` | `temp` |
| `write_back` | `true` |

The sample config intentionally overrides several of these values.

## Agent slots

The main config always uses the same fixed agent slots:

- `execution`
- `reviewer_1`
- `reviewer_2`
- `review_decision`
- `autonomy_decision`
- `loop_detector`

All six `agents.<key>` entries are required, even when most role metadata and prompt files come from the prompt pack.

`review_decision` must define `merged_review_output_path_template`.

`autonomy_decision` must define `decision_report_output_path_template`.

## Prompt packs

`program.prompt_pack` selects the active prompt pack. Built-in packs are:

- `implementation`
- `planning`

Each prompt pack has a `pack.yaml` file that defines each agent's:

- `role_name`
- `output_name`
- `prompt_file`

The `implementation` pack is intended for code implementation, script changes, tests, and bug fixes:

- `reviewer_1` maps to `reviewer-requirements`
- `reviewer_2` maps to `reviewer-quality`

The `planning` pack is intended for solution design, execution planning, documentation drafts, architecture work, and process planning:

- `reviewer_1` maps to `reviewer-completeness`
- `reviewer_2` maps to `reviewer-risk`

Default English prompt pack layout:

- `prompts/en/implementation/pack.yaml`
- `prompts/en/implementation/execution.md`
- `prompts/en/implementation/reviewer-requirements.md`
- `prompts/en/implementation/reviewer-quality.md`
- `prompts/en/implementation/review-decision.md`
- `prompts/en/implementation/autonomy-decision.md`
- `prompts/en/implementation/loop-detector.md`
- `prompts/en/planning/pack.yaml`
- `prompts/en/planning/execution.md`
- `prompts/en/planning/reviewer-completeness.md`
- `prompts/en/planning/reviewer-risk.md`
- `prompts/en/planning/review-decision.md`
- `prompts/en/planning/autonomy-decision.md`
- `prompts/en/planning/loop-detector.md`

## Prompt pack resolution

Prompt files are resolved independently from the config file location.

Resolution order:

1. `--prompt-pack-path/<program.prompt_pack>`, if `--prompt-pack-path` is provided;
2. `program.prompt_pack_path/<program.prompt_pack>`, if `program.prompt_pack_path` is provided;
3. `prompts/en/<program.prompt_pack>` next to the config file, if it contains `pack.yaml`;
4. built-in `prompts/en/<program.prompt_pack>` next to `main.py`.

`program.prompt_pack_path` must point to a prompt packs directory whose child directory named by `program.prompt_pack` contains `pack.yaml`. For example, with `program.prompt_pack: implementation`, `program.prompt_pack_path: prompts/en` resolves to `prompts/en/implementation/pack.yaml`, while `program.prompt_pack_path: prompts/cn` resolves to `prompts/cn/implementation/pack.yaml`. Relative paths are resolved from the config file directory.

CLI `--prompt-pack-path` has the same format and overrides `program.prompt_pack_path`. Relative CLI paths are resolved from the current working directory.

An explicitly provided prompt packs path is strict: if `<path>/<program.prompt_pack>/pack.yaml` does not exist, loading fails instead of silently falling back.

## Prompt and role overrides

In the common case, the main config does not need to define `role_name` or `prompt_template_path` for every agent. Switching `program.prompt_pack` is enough.

For compatibility, YAML can still override these per agent:

- `prompt_template`
- `prompt_template_path`
- `role_name`
- `output_name`

The pack manifest is the preferred source of role metadata.

`prompt_template_path` supports these variables:

- `${agent_key}`
- `${prompt_pack}`
- `${prompt_pack_dir}`

## Models and variants

The workflow always passes `--model` to OpenCode, using either `agents.<agent>.model`, `program.default_model`, or the built-in default.

The workflow passes `--variant` only when a resolved variant exists. Variant priority:

1. `agents.<agent>.variant`
2. `program.default_variant`, only when the agent's actual model is `program.default_model`
3. no `--variant`

If no variant is resolved, OpenCode uses its own default variant behavior for the supplied model.

## Output path templates

Output path templates can use these variables:

- `${agent_key}`
- `${agent_output_name}`
- `${topic}`
- `${cycle_number}`
- `${iteration_number}`
- `${job_temp_dir}`

`${agent_output_name}` changes with the selected pack. For example, the `implementation` pack uses `reviewer-requirements` / `reviewer-quality`, while the `planning` pack uses `reviewer-completeness` / `reviewer-risk`.

## Jobs file behavior

Jobs file shape:

```yaml
jobs:
  - topic: api-service-design
    task: |
      Produce an API service execution plan.
    status: pending
```

Rules:

- `topic` must be unique.
- `topic` is used as part of the job temp directory path; prefer path-safe names such as `api-service-design` and avoid path separators or `..`.
- `task` must be a non-empty string.
- `status: done` and `status: needs_human_review` are terminal and skipped.
- Any other status is executable.
- `human_review`, when present, must be a non-empty string.

You can manually add a multiline `human_review: |` field as feedback. It does not replace the original `task`; it is passed to agents together with the original task after you explicitly confirm it should be used for the current round.

If `write_back: true`, the workflow writes back only job status changes:

- `running`: execution started
- `done`: job completed
- `needs_human_review`: human-confirmed stop, or no safe way to continue automatically
- `failed`: execution failed

The workflow does not remove `human_review` from the jobs YAML file.
