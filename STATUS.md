# Status

| | |
|---|---|
| Maturity | research result, Internally reproduced (committed results; not externally reproduced) |
| Version | none: no tags or releases |
| Last git activity | 2026-09-25 (`git log -1 --format=%cs`; clone is shallow) |
| Activity | 6 commits between 2026-07-15 and 2026-10-15 (GitHub API) |
| Default branch | `master` |
| Licence | MIT, "Copyright (c) 2026 ReDevOps" (`LICENSE`) |

## Result and scope

Across 11 paired comparisons, gate-pass was 11/11 in both arms and cost, quality and latency showed no consistent difference; the write-up concludes the evidence does not support adopting formal runtime-managed orchestration as a blanket mechanism. Scope caveats (from the write-up): N=11 paired, toy-scale Kotlin tasks, one judge family, one manager model, worker token cost not metered, injections recovered by both arms.

## Supported

- `python3 validate_gates.py` (needs `kotlinc` and JDK 17+, no API keys) and `harness/bench.py` for paired trials (needs API keys in the environment).

## Not supported / not established

- No re-run was done for this documentation change; the results have not been reproduced outside ReDevOps.
- The result is a mechanism test, not a leaderboard, and does not measure governed-runtime properties such as authority, approval, replay or audit.

## Known issues

- Before this change the README's `sidekick` link pointed at the organisation root instead of the repo.
