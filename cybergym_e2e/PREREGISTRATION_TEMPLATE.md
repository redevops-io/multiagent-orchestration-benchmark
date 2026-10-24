# Pre-registration template (fill in and commit BEFORE the run)

Copy to `cybergym_e2e/prereg/<name>.md`, fill every field, commit, and only then start the run. A field changed after seeing results is a deviation and is listed as one in the report.

1. **Question.** One sentence. Example: does arm B solve more `e2e` tasks (S1+S2+S3) than arm A on the same tasks, holding model, prompt exposure and caps fixed?
2. **Arms.** Name each, its scaffold and what it can see. Say which are upstream scaffolds and which are ours. State why each is "technically equivalent" to the others, or why the comparison is not made.
3. **Held constant.** Model id(s) and provider, prompt text hash, per-task time limit, per-task cost cap, tools, network allowlist, `max_attempts = 1`.
4. **Tasks.** The sample file, its seed, the upstream revision and the task-list hash (from `PINS.json`). How the sample was drawn and why its size.
5. **Primary outcome.** Success as defined in the README (S3 for `patch-only`; S1+S2+S3 for `e2e`). S4 is diagnostic and is reported, not tested.
6. **Primary comparison.** Which arms, which mode, which statistic (paired discordant counts and the exact McNemar p), the significance level and what counts as a meaningful difference.
7. **Denominators.** Every attempted task, and tasks without an infrastructure error; both are reported. How an infrastructure failure is retried (at most how many times, and a retried task keeps one row).
8. **Exclusions.** The only reasons a task may be excluded, decided now. Anything else stays in.
9. **Stopping rule.** The sample size and when the run ends; no peeking-based stopping.
10. **Leakage handling.** What a hard leak does (the attempt is invalid and reported, not re-run quietly) and who reads the review items.
11. **What would change the plan.** A cost overrun, a runner fault, an upstream change.
12. **What is out of scope.** Anything not measured (other benchmarks, other modes, real-world targets).
