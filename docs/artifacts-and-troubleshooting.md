# Artifacts and troubleshooting

This document summarizes output files, JSON schemas, retry behavior, and common failure modes.

## Output location

By default, the workflow writes artifacts under:

```text
<jobs-file-dir>/<temp_dir>/runs/<run_id>/<topic>/
```

If `temp_dir` is relative, it is resolved relative to the `--jobs` file directory, not the program directory.

Every run gets a timestamp-based `run_id`. All jobs in the same run are stored under that run directory for traceability and isolation.

## Typical artifacts

Per job, expect files such as:

- `execution-cycle-<n>-iteration-<m>.txt`
- reviewer output files, with names depending on the active prompt pack
- `review-decision-cycle-<n>-iteration-<m>.txt`
- `review-decision-cycle-<n>-iteration-<m>.review.txt`
- `adjudication-memory.latest.txt`
- `autonomy-decision-cycle-<n>-iteration-<m>.txt`, when autonomy runs
- `autonomy-decision-cycle-<n>-iteration-<m>.report.md`, when autonomy runs
- `loop-detector-cycle-<n>-iteration-<m>.txt`, when loop detection runs

Exact names come from agent output path templates in the config.

## JSON validation

Decision outputs must be valid JSON objects with the documented required keys and allowed `next_action` values. Prompt templates instruct agents not to emit extra keys, but hard validation for `review_decision` and `loop_detector` focuses on required keys, non-empty reasons, and allowed actions. `autonomy_decision` is stricter and rejects unsupported extra keys.

### `review_decision`

Required shape:

```json
{"next_action":"rerun_execution","reason":"..."}
```

```json
{"next_action":"human_review","reason":"..."}
```

```json
{"next_action":"done","reason":"..."}
```

`review_decision` must also write a non-empty merged review file and a valid `adjudication-memory.latest.txt` file.

### `loop_detector`

Required shape:

```json
{"next_action":"continue","reason":"..."}
```

```json
{"next_action":"human_review","reason":"..."}
```

### `autonomy_decision`

Exactly one of:

```json
{"next_action":"auto_resolve","reason":"...","resume_feedback":"..."}
```

```json
{"next_action":"human_review","reason":"...","resume_feedback":""}
```

`autonomy_decision` must also write a Markdown report starting with `# Autonomy Decision Report`.

## Adjudication memory

Each job temp directory maintains:

```text
adjudication-memory.latest.txt
```

`review_decision` overwrites it on every round. Reviewers read it in later iterations. It records issues already adjudicated as `rejected` or `deferred_human`, plus conditions under which they may be raised again.

The file must start with:

```md
# Adjudication Memory
```

and contain:

```text
[REJECTED_OR_DEFERRED_ISSUES]
```

It must include either at least one issue entry or `- 无`.

## CLI result JSON

At the end of a run, the CLI prints a JSON summary containing:

- `run_id`
- overall `status`: `completed` or `completed_with_failures`
- `config_file`, `jobs_file`, and run-level `temp_dir`
- `completed_jobs`
- `failed_jobs`
- `skipped_jobs`
- `summary` counts for completed, failed, and skipped jobs

A single job failure does not stop the whole jobs file. The workflow marks that job `failed` when `write_back: true`, records it under `failed_jobs`, and continues with later jobs.

Terminal jobs with `status: done` or `status: needs_human_review` are recorded under `skipped_jobs`.

## Retry behavior

- OpenCode subprocess failures are retried up to 5 attempts per agent call, with a short backoff.
- If an agent call completes but fails to produce the expected output artifact or JSON schema, that step is retried up to 3 attempts with the same agent session.
- If retries are exhausted, the current job fails and the workflow moves on to the next executable job.

## Common troubleshooting

### `ModuleNotFoundError: No module named 'yaml'`

Install dependencies or run through `uv`:

```bash
uv sync
uv run python main.py --help
```

Without `uv`, install `pyyaml` in your active Python environment.

### OpenCode attach or provider errors

Check that:

- `program.opencode_bin` points to a working OpenCode executable;
- the OpenCode attach server is running at `program.attach_url`;
- model/provider credentials are configured;
- the configured model and optional variant are accepted by your OpenCode/provider setup.

### Prompt pack not found

Check `program.prompt_pack`, `program.prompt_pack_path`, or `--prompt-pack-path`.

An explicit prompt pack path must point to a prompt packs directory containing `<program.prompt_pack>/pack.yaml`.

### Agent output schema failures

Inspect the failing agent's output artifact and the log message. Common causes:

- JSON file contains Markdown fences or explanatory text;
- autonomy decision JSON contains unsupported extra keys;
- required output files were not written;
- merged review, autonomy report, or adjudication memory is empty;
- adjudication memory does not follow the required heading/section format.

The workflow retries the same agent step up to 3 times before failing the job.

### Human review appears to reuse stale feedback

`human_review` remains in the jobs YAML for inspection, but runtime feedback is cleared before each prompt for human feedback. The workflow injects it only after you explicitly confirm it should be used for the current round.

If feedback seems stale, check whether you confirmed `y` at the prompt and whether the current jobs YAML contains the intended `human_review` value.
