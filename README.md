# Multi-Agent Orchestration Benchmark

[![NVIDIA Inception](https://img.shields.io/badge/NVIDIA-Inception%20Program%20Member-76B900.svg)](https://www.nvidia.com/en-us/startups/)

Does **formal runtime-managed orchestration** (isolation contracts + a runtime *integration mission*)
actually beat **emergent supervisor-led orchestration** (one strong manager decomposing work, running
parallel workers, and integrating their output) — for real software-engineering tasks?

This repo is the full, re-runnable harness behind that experiment. It is deliberately designed so the
**only variable is the orchestration layer**: the manager model, the worker pool, and the task topology
are held constant across the two arms.

## The hypothesis
Formal isolation + a runtime integration mission will **not** beat emergent supervisor-led parallelism
on final code quality or quality-adjusted cost/latency unless controlled trials show a measurable,
gate-passing improvement. (We adopt a formal mechanism only if it earns its keep; ties default to the
simpler system.)

## The setup (one variable)
| Role | Both arms |
|---|---|
| **Manager** | one fixed model, both arms (decomposes, briefs, reviews, integrates — never writes feature code) |
| **Workers** | a fixed heterogeneous pool, run as isolated single-task agents in git worktrees |
| **Worker assignment** | random, seeded, **paired** (identical mapping in both arms), deliberately NOT skill-matched |
| **Topology** | predeclared per task shape, identical in both arms |
| **Judge** | an independent model family (writes none of the code); **blinded, pairwise**; secondary to the deterministic gates |

- **Arm A (emergent):** natural-language briefs; the manager integrates worker parts directly.
- **Arm B (formal):** exact-contract briefs; a runtime *integration mission* runs a declared-output
  check + a one-shot recovery retry + a compile-probe of the concatenated parts, then feeds that
  **integration state** to the manager before it merges. Semantic authority stays with the manager.

The exact frozen model IDs, source revisions, budgets, and the decision rule are in
[`spec/frozen_config.json`](spec/frozen_config.json). The gates + judge rubric are in
[`spec/gates_and_rubric.md`](spec/gates_and_rubric.md).

## Task shapes (Kotlin)
Three shapes at different dependency structures (see `spec/<shape>/`):
- **independent_modules** — 4 independent utilities (high parallelism, low integration stress).
- **shared_contract** — canonical schemas + one serialization contract used by several parts (contract drift).
- **cross_cutting** — route retrieval through a Capability interface; the boundary is enforced
  *functionally* (a fake-Retriever test fails if a worker hard-wires the concrete class).

Two failure injections (`spec/<shape>/injections.json`): an incompatible shared-contract assumption,
and a capability-boundary violation.

## Deterministic gates (decided first, before any judgment)
Per solution: **build** (`kotlinc`), **correctness** (a self-contained hidden-test `main()`, ≥90% pass),
and **architecture** (require/forbid static scan). Hidden tests live in `spec/<shape>/hidden_tests.kt`
and are validated against known-good reference solutions in `reference/`.

## Reproduce
Prereqs: `kotlinc` + `java` (JDK 17+) on PATH; Python 3.10+.

```bash
# 1. gates are correct (no API keys, no model calls):
python3 validate_gates.py

# 2. run a paired trial (needs API keys in the environment — NEVER commit them):
export OPENAI_API_KEY=...      # manager
export XAI_API_KEY=...         # worker (grok)
export KIMI_API_KEY=...        # worker (kimi, via the Moonshot OpenAI-compatible endpoint)
python3 harness/bench.py shared_contract 42                    # one paired A/B trial, seed 42
python3 harness/bench.py cross_cutting 42 capability-boundary-violation   # with an injection

# results land in runs/<tag>/summary.json (runs/ is gitignored)
```

Workers are driven through the [`sidekick`](https://github.com/redevops-io) CLI (a local coding-agent
orchestrator: `--provider grok --grok-model/-base-url/-key`, worktree-isolated). Point `--grok-base-url`
at any OpenAI-compatible endpoint to swap worker models.

## Results & write-up
Aggregate results are in `results/`; the narrative write-up (hypothesis → setup → conclusions) is
[`writeup/does-formal-orchestration-help.md`](writeup/does-formal-orchestration-help.md). **Scope matters:** read the caveats in the results — small N, specific shapes, one
judge family. This measures a mechanism, not a leaderboard.

## Note on keys
No credentials are stored in this repo. All API keys are read from environment variables at run time
(`os.environ`); `runs/`, `.env`, and `*.key` are gitignored.
