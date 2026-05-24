# Workflow mechanics

This document describes the runtime behavior of `council-agentflow`. For setup and basic usage, start with the root [`README.md`](../README.md).

## Core concepts

- An **iteration** is one `execution -> reviewers -> review_decision` pass.
- A **cycle** is a window of iterations that share the same OpenCode agent sessions.
- A **job** can run one or more cycles, controlled by `program.max_rounds`.
- `program.max_iterations_per_cycle` limits automatic reruns inside one cycle.

Default behavior is conservative: `max_rounds` defaults to `1`, and a cycle that reaches `done` still pauses at a human review gate before the job is marked complete or the workflow advances.

## Main iteration flow

1. `execution` performs the task and writes its final response to a plain-text temp file.
2. `reviewer_1` and `reviewer_2` read the `execution` output and review it independently.
3. Each reviewer writes a plain-text review artifact.
4. `review_decision` reads both reviewer outputs, filters and adjudicates the feedback, and writes:
   - a JSON decision file: `{"next_action":"rerun_execution|human_review|done","reason":"..."}`;
   - a merged plain-text review file for the next `execution` step;
   - the latest adjudication memory.
5. If `next_action=rerun_execution`, the workflow stays in the current cycle and starts the next iteration.
6. If `next_action=human_review`, the workflow normally calls `autonomy_decision` before asking for real human input.
7. If `next_action=done`, automatic work for the current cycle stops and the cycle-end human review gate runs.

On rerun, `execution` reads the merged review produced by the previous `review_decision`. It should not read the two raw reviewer outputs directly.

## Cycle-end `done` gate

`done` means “complete under the current context.” It does not immediately stop the whole job.

When a cycle reaches `done`, the orchestrator asks whether `human_review` has been added or updated in the jobs YAML:

- If yes, it reloads that `human_review` value and continues with the next iteration in the **current cycle**.
- If no, the current cycle is completed. The job is marked `done` if this was the last configured cycle, or advances to the next cycle if more cycles remain.

The cycle-end gate prompts directly and does not call `autonomy_decision`.

## Human review branch

Automatic `human_review` transitions normally go through `autonomy_decision` first. This includes requests from:

- `review_decision`;
- `loop_detector`;
- the `max_iterations_per_cycle` safety stop.

If `autonomy_decision.next_action=auto_resolve`, its `resume_feedback` is injected into the next `execution` step in the current cycle. If the current cycle has already reached its iteration limit, the workflow automatically adds one extra iteration to apply the feedback.

If `autonomy_decision.next_action=human_review`, or if human review was forced from the console, the workflow asks whether a `human_review` field has been added or updated in the jobs YAML:

- If yes, it reloads the current job's `human_review` value, combines it with the original `task`, and continues with the next iteration in the **current cycle**.
- The `human_review` field is not removed from the jobs YAML file. To avoid stale feedback leaking into later rounds, active runtime feedback is cleared before the workflow asks for human feedback again.
- If the current cycle has already reached its iteration limit, the workflow asks how many extra iterations to add before continuing the same cycle.
- If no human feedback is available and there are remaining cycles, the workflow asks whether to move on to the next cycle:
  - enter `y` to end the current cycle and start the next one;
  - enter `n` to stop the current job and mark it as `needs_human_review`.

If `human_review` is triggered in the final cycle and no human feedback is provided, the job is marked `needs_human_review`.

## Loop detector

The workflow calls `loop_detector` when all of the following are true:

- `iteration_number >= 4`;
- the current iteration number is even (`iteration_number % 2 == 0`);
- the last two `review_decision.next_action` values were both `rerun_execution`.

`loop_detector` compares the latest two rounds of execution output, reviewer outputs, review decisions, merged reviews, and adjudication memory to decide whether the workflow has entered a low-value loop.

Its JSON output must include one of these `next_action` values:

```json
{"next_action":"continue","reason":"..."}
```

```json
{"next_action":"human_review","reason":"..."}
```

If it returns `human_review`, the workflow enters the autonomy/human-review handling path.

## Console-forced human review

During automatic execution, the workflow polls console input only at **iteration boundaries**. It does not interrupt an active OpenCode call.

If you enter any of the following commands during a run and press Enter, the workflow forces the next step to `human_review` after the current iteration finishes:

- `human`
- `hr`
- `人工`

Console-forced human review skips `autonomy_decision` and goes directly to the human-review branch. Unsupported console input is ignored and logged.

## Sessions and cross-cycle state

Each agent reuses its own OpenCode session within a cycle, across all iterations and steps.

When a new cycle starts:

- all agents get fresh sessions;
- previous-cycle merged reviews are not automatically injected into prompts;
- `review_decision_previous_*` does not point to previous-cycle output.

Cross-cycle state only comes from:

- formal workspace changes;
- the original task;
- explicitly confirmed `human_review`;
- file paths explicitly provided in the task or human feedback.

Previous-cycle temp files stay on disk for traceability, but they are not automatically used as prompt input for the next cycle.

Treat `max_rounds>1` as an advanced mode for independent re-review or second-pass hardening. For normal one-off tasks, keep `max_rounds=1`.
