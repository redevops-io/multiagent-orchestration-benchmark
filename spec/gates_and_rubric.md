# Gates (deterministic, PRIMARY) and blinded judge rubric

## Deterministic gates — decided first, before any judgment
| Gate | Check (automated) | Fail = |
|---|---|---|
| Build | `kotlinc` compiles the module + tests clean, no network at gate time | hard fail |
| Correctness | hidden acceptance tests pass rate >= 0.90 | hard fail |
| Architecture | static import scan: consumers coupled only via the shared contract; no prohibited imports; `Serde` used everywhere | hard fail |
| Reliability | static analysis (detekt/ktlint) — no severe concurrency/data-integrity/error-handling findings | hard fail |
| Replay | run reconstructs from repo state + config + prompts + traces + seed | hard fail |
| No-loss | a formal mechanism is NOT adopted if it improves observability but lowers final quality | adoption block |

## Blinded pairwise judge (Claude) — SECONDARY, among gate-passing runs only
Inputs: the two arms' final code for the SAME task, arm identifiers + traces stripped. Output: which
is better + why + concrete defects. Scored dimensions (pairwise preference, not absolute):
- code quality (idiomatic Kotlin, type safety, modularity, API clarity, no gratuitous abstraction)
- architectural fidelity (boundaries, contract discipline, provenance)
- test quality (behavioural coverage, negative + edge cases)
Judge sees ARTIFACTS only, never which arm is formal.

## Secondary metrics (recorded, not gating)
coordination overhead (manager tokens, worker tokens, rejection count + rejection accuracy, duplicate
work, contract conflicts, repair time); cost (marginal per-run) + latency + time-to-first-passing-build;
recoverability (outcome + added cost after injection); reproducibility (variance across paired trials).
