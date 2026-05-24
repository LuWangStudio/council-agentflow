# Autonomy decision policy

`autonomy_decision` is a bounded policy gate. It reviews automatic human-review requests and decides whether the current blocker can be resolved automatically under delegated authority, or whether real human input is required.

It is policy, not execution: it should not implement the task itself. It decides whether and how the workflow may continue.

## When it runs

The workflow calls `autonomy_decision` by default when `human_review` is returned by:

- `review_decision`;
- `loop_detector`;
- the `max_iterations_per_cycle` safety stop.

It is skipped when human review is forced from the console with `human`, `hr`, or `人工`.

The cycle-end `done` gate also prompts directly and does not call `autonomy_decision`.

## Inputs

`autonomy_decision` reads the latest:

- execution output;
- reviewer outputs;
- review decision JSON;
- merged review;
- adjudication memory;
- loop detector output, when applicable.

It then reviews the human-review request itself: whether the blocker really requires external judgment, or whether it can be handled safely inside the current job/task boundary.

## Output schema

Its JSON output must be exactly one of:

```json
{"next_action":"auto_resolve","reason":"...","resume_feedback":"..."}
```

```json
{"next_action":"human_review","reason":"...","resume_feedback":""}
```

Rules:

- `next_action` must be `auto_resolve` or `human_review`.
- `resume_feedback` must be non-empty for `auto_resolve`.
- `resume_feedback` must be empty for `human_review`.
- Unsupported extra JSON keys are rejected.

It also writes a Markdown decision report named by `decision_report_output_path_template`. The report must start with:

```md
# Autonomy Decision Report
```

The report records the policy basis, delegated-review assessment, constraints, verification method, rollback plan, and any questions that require human input.

## `auto_resolve` behavior

If `autonomy_decision` returns `auto_resolve`:

- `resume_feedback` is injected into the next `execution` step as autonomy-decision feedback.
- The previous merged review remains available as adjudicated context for anything not overridden by autonomy feedback.
- If the current cycle has already reached its iteration limit, the workflow automatically adds one extra iteration to apply the feedback.

## Policy model

The policy is bounded default-deny. `auto_resolve` is allowed only when the decision is:

- low-cost;
- limited in scope;
- testable or reviewable;
- reversible, correctable, or safe to revisit;
- supported by available evidence;
- within the current job/task boundary;
- free of new security, privacy, compliance, dependency, public API, or protocol-compatibility risk.

Within the current job/task boundary, `autonomy_decision` may act as a conservative delegated decision-maker for the user. It may resolve low-risk review/signoff/checklist-style blockers when the artifacts, rubric, evidence, and rollback path are clear.

It should not escalate only because a blocker contains words such as “signoff”, “owner review”, or “subjective review”. It must first evaluate whether delegated review is safe.

## Typical auto-resolvable cases

For implementation tasks:

- choose the implementation approach most consistent with existing project patterns;
- choose a simple no-new-dependency solution;
- preserve public API and behavior compatibility;
- add small tests, boundary checks, or error handling;
- make low-risk fixes inside existing architecture boundaries;
- give delegated signoff or fail-with-feedback for clear checklist items.

For planning tasks:

- choose the most conservative, lowest-cost, reversible planning assumption;
- downgrade unknowns into assumptions, constraints, or validation items;
- choose a path that does not add dependencies, architecture boundaries, public APIs, or incompatible data/protocol changes;
- add validation methods, acceptance criteria, risks, or rollback strategy;
- give delegated signoff or fail-with-feedback against a clear rubric.

## Must escalate to human review

Return `human_review` when the decision requires real external authority or unsafe assumptions, including:

- missing review object, key context, or readable evidence;
- no clear rubric and no safe conservative default;
- customer approval;
- legal, compliance, security, budget, or organizational approval;
- cross-team commitments;
- irreversible or high-impact decisions;
- significant cost increases;
- new dependencies;
- new architecture boundaries;
- new public APIs;
- incompatible data model or protocol changes;
- multiple reasonable options with no evidence-based conservative ordering;
- guessing user preference, product intent, external facts, or organization constraints.

Subjective product, design, or documentation judgments may be handled automatically only when the task provides enough artifacts and review criteria for conservative delegated decision-making.

## Human-review result

If `autonomy_decision` returns `human_review`, the workflow enters the human-review branch. The human can add or update `human_review` in the jobs YAML, then confirm that it should be used as feedback for the current round.
