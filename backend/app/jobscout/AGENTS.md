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
