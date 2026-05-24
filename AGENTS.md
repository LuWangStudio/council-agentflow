# AGENTS.md

This file provides baseline project context for coding agents. Do not treat it as complete documentation; for fuller and more up-to-date behavior, read `README.md` first.

## Project Overview

- Project name: `council-agentflow`
- Type: YAML-driven Python CLI.
- Goal: run a fixed-loop multi-agent workflow where `execution`, multiple `reviewer` agents, `review_decision`, `autonomy_decision`, `loop_detector`, and related roles collaborate according to configuration.
- Entry point: `main.py`
- Main configuration and assets:
  - `example.config.workflow.yaml`: sample common workflow / agent configuration.
  - `example.jobs.yaml`: sample jobs configuration.
  - `prompts/`: prompt packs and agent prompt templates.

## Required Reading

Before making changes, read:

1. `README.md`: project behavior, run commands, prompt packs, autonomy decision, loop detector, jobs behavior, and output directory behavior.
2. If changing prompt behavior, also read the relevant prompt pack directories:
   - `prompts/implementation/`
   - `prompts/planning/`

If this file conflicts with `README.md` or the actual code behavior, treat `README.md` and the code as authoritative. Update this file when needed.

## Run Command

```bash
python3 main.py --config example.config.workflow.yaml --jobs example.jobs.yaml
```

## Python / Tooling Context

- This project is a Python CLI. `pyproject.toml` currently declares `requires-python = ">=3.11"`; the main dependency is `pyyaml`.
- The repo contains `uv.lock`; local development should prefer `uv` or the project virtual environment.
- If the system Python lacks dependencies, try:

```bash
uv run python main.py --help
```

or, with an existing virtual environment:

```bash
.venv/bin/python main.py --help
```

- After changing Python code, run at least syntax/import-level validation:

```bash
.venv/bin/python -m compileall main.py agentflow_core
.venv/bin/python main.py --help
```

## Coding Standards and Implementation Conventions

- Keep the CLI entry point thin: `main.py` should only call `agentflow_core.cli.main`; argument parsing, config loading, and workflow execution belong in their respective modules.
- Follow the existing module boundaries:
  - `config.py`: YAML config/jobs loading, validation, and job status write-back.
  - `workflow_runner.py` / `workflow_cycle_runner.py` / `workflow_human_review.py`: run/job/cycle/human-review orchestration.
  - `workflow_steps.py`: individual agent step prompt rendering, invocation, and output validation.
  - `workflow_io.py`: file reading, JSON text payload loading, and temp directory reset.
  - `opencode_runner.py` / `session_store.py`: OpenCode subprocess and session-related logic.
- Preserve the current Python style: `from __future__ import annotations`, type annotations, dataclass config objects, `pathlib.Path`, and explicit `encoding="utf-8"`.
- Validate configuration early and strictly. Configuration problems should raise `ConfigError`, and error messages should include YAML field paths where possible. Do not silently accept malformed config.
- Prompt rendering should go through `render_prompt` / `string.Template`. When adding prompt variables, prefer centralizing common variables in `build_common_prompt_vars`; inject only step-specific variables in the relevant step.
- Agent outputs are file artifacts. Do not infer business results from model stdout. A step should follow the pattern: render output paths -> render prompt -> invoke agent -> read and validate output.
- JSON outputs must be strictly validated. If adding or changing `next_action` values or JSON schemas, update `workflow_constants.py`, the relevant validators, prompt templates, README, and examples together.
- Keep OpenCode invocation encapsulated in `OpencodeRunner` / `session_store`. Workflow orchestration layers should not scatter direct `subprocess` calls.
- Use `log_step`, `log_verbose`, and `log_raw_event` for logging. Avoid ad hoc `print` calls except for final CLI JSON output and error output.
- Keep exception types clear: configuration problems use `ConfigError`, workflow/agent output problems use `WorkflowError`, and OpenCode invocation problems use `OpencodeError`.
- Make session and state passing explicit. Reuse sessions within the same cycle through `runner.sessions`; do not rely on old temp outputs as implicit state across cycles.
- When changing workflow mechanics, also check whether config, prompt pack manifests, prompt templates, README, and sample jobs need updates.

## Core Workflow Model

- An iteration is roughly: `execution -> reviewer_1/reviewer_2 -> review_decision`.
- A cycle is a window of iterations that reuse the same agent sessions.
- The default `max_rounds=1`. A job normally finishes after the current cycle reaches `done` and the cycle-end human review gate confirms that no additional feedback should be injected.
- `review_decision` outputs:
  - a strict JSON decision file where `next_action` is `rerun_execution`, `human_review`, or `done`;
  - a merged plain-text review file for `execution` to read.
- Human-review requests from `review_decision` or `loop_detector` normally go through `autonomy_decision` first, unless human review was forced from the console. The cycle-end `done` gate prompts for optional human feedback directly and does not call `autonomy_decision`.
- A new cycle resets agent sessions. Temp outputs from the previous cycle are not automatically injected into the new prompt.

## Change Principles

- Prefer simple, readable, testable implementations.
- Separate mechanism from policy: workflow orchestration, file passing, and session lifecycle are mechanism; permission for automatic decisions, human boundaries, and risk assessment are policy.
- Do not let reviewers bypass `review_decision`. When `execution` reruns, it should read the merged review, not the two raw reviewer outputs directly.
- For `autonomy_decision`, keep a bounded default-deny policy: within the current task/job boundary, it may act as a conservative delegated decision-maker for low-risk, recorded, reviewable review/signoff/checklist blockers, but only with enough evidence/rubric. Items that require an external owner, customer, legal, security, compliance, budget, or organizational authorization must still go to human review.
- Do not depend on historical artifacts in temp directories as implicit cross-cycle state. Any information that must carry across cycles should be written to formal workspace files or explicitly included in the task / `human_review`.

## Common Change Entry Points

- Workflow / orchestration behavior: start with `main.py` and `agentflow_core/`.
- Prompt pack or agent role semantics: check `prompts/<pack>/pack.yaml` and the corresponding `.md` prompt files.
- Sample config or jobs behavior: check `example.config.workflow.yaml`, `example.jobs.yaml`, and `README.md`.
