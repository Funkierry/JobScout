# JobScout evidence and matching

Keep app behavior isolated from the reusable harness. Matching uses only current
owner/thread snapshots and successfully read uploads. `qualification_checks` shares
the exclusion decision and its source spans; future, negated or missing qualifications
stay unknown. Grounded low proficiency may explain a zero-score item, never earn
points. Preserve paired evidence, independent dimensions and zero-score exclusion.

`score_matches(excluded=...)` supplies at most 10 rejected records to the fixed
report, never audit metadata. Grouped summaries put scores in each record's detail;
only the recommendation table feeds candidate persistence. Keep runtime and grader
replay identical. Render all source text through `cell`; ungrounded quotes must not
reappear in explanations. See `docs/jobscout/requirements-and-recovery.md`.

Preparation actions stay inside the existing final report section. Select only
validated requirement/gap items, preserve source links and grounded resume quotes,
and label ordering/actions as suggestions, not interview schedules or probabilities.
Personal notes and checkboxes live outside evidence Markdown; exports of the report
must not silently include those private notes. See `docs/jobscout/actions-and-feedback.md`.

Matching contract v2 checks term-local qualifiers in the actual uploaded resume
and all relevant job fields, including when a model cites a cropped phrase or
uses the direction dimension. Repeated ambiguous short quotes keep all contexts.
Do not confuse headings or another skill with this skill's proficiency. Match
history/export requires contract v2 so v1 recommendations prompt regeneration.
Real model artifacts and resumes remain under ignored local_eval; CI uses only
synthetic regressions. See docs/jobscout/live-model-evaluation.md for scope.
Only the exact /mnt/user-data/uploads/ prefix may alias a key in already-read
resumes; never accept basename extraction from another path or authorize a read.
