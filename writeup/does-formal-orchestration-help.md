# Does formal multi-agent orchestration beat a good supervisor?

*A small, controlled, reproducible benchmark — run before adding a multi-agent framework to a roadmap.*

## Why we ran this
Modern "multi-agent" frameworks push formal machinery: rigid isolation contracts, deep agent
hierarchies, generic semantic **merge** operators, runtime-managed integration. Before committing that
to a roadmap, we wanted evidence for one specific question:

> Does **formal, runtime-managed orchestration** actually beat a **strong supervisor decomposing work,
> running parallel workers, and integrating their output** — for real software-engineering tasks?

## The hypothesis (pre-registered)
Formal isolation + a runtime integration mission will **not** beat emergent supervisor-led parallelism
on correctness, code quality, or quality-adjusted cost/latency **unless** controlled trials show a
measurable, gate-passing improvement. Ties default to the simpler system.

## The setup — one variable
The whole design holds everything constant except the orchestration layer, so any difference is
attributable to *that* and not to the models.

| Role | Configuration (identical across both arms) |
|---|---|
| **Manager** | `gpt-5`, both arms. Decomposes, briefs, reviews, integrates — never writes feature code. We used a *representative* manager on purpose: a too-strong manager could paper over orchestration lapses and hide the effect. |
| **Workers** | `grok-4.5` + `kimi-k2.7-code`, run as single-task agents in isolated git worktrees (via the `sidekick` CLI). |
| **Worker assignment** | Random, seeded, **paired** — the identical subtask→model map in both arms, deliberately NOT skill-matched, so worker quality can't mask the integration difference. |
| **Topology** | Predeclared per task shape, identical in both arms. |
| **Judge** | `claude`, an independent family that writes none of the code. **Blinded, pairwise, randomized order.** Secondary to the deterministic gates. |

- **Arm A — emergent:** natural-language briefs; the manager integrates worker parts directly.
- **Arm B — formal:** exact-contract briefs; a runtime *integration mission* runs a declared-output
  check, a one-shot recovery retry, and a compile-probe of the concatenated parts, then feeds that
  **integration state** to the manager before it merges. Semantic authority stays with the manager.

**Tasks:** three Kotlin shapes at different dependency structures — `independent_modules` (high
parallelism), `shared_contract` (contract drift), `cross_cutting` (route retrieval through a Capability
interface; the boundary is enforced *functionally* by a fake-Retriever test). **11 paired comparisons**
total: 3 shapes × 3 seeds + 2 failure injections (an incompatible shared-contract assumption, and a
capability-boundary violation).

**Gates (deterministic, decided first):** `kotlinc` build, a self-contained hidden-test `main()` (≥90%),
and a require/forbid architecture scan. The blinded judge only breaks ties among gate-passing solutions.

## An honesty checkpoint worth telling
Our first main block *looked* like the formal arm won (3/3 vs 2/3). It was a lie the harness told us:
the manager's integration call had a token cap too low for a reasoning model, so on one run GPT-5 spent
the whole budget "thinking" and returned empty content → a 0-byte solution → a false gate failure for
the emergent arm. We traced it, fixed the harness (escalate the budget on empty output; treat
persistent-empty as a *harness error*, never an arm failure), and re-ran uniformly. Reporting that
artifact as a result would have been the easiest — and most wrong — thing to do.

## Results
Across all 11 paired comparisons:

| Dimension | Arm A (emergent) | Arm B (formal) | Winner |
|---|---|---|---|
| Gate-pass (build + hidden tests + architecture) | **11/11** | **11/11** | tie |
| Manager cost (tokens) | $0.318 | $0.320 | tie |
| Worker latency, mean / median | 243s / ~116s | 216s / ~104s | ~tie |
| Blinded pairwise quality | **5 wins** | **5 wins** | tie (+1 tie) |
| Both failure injections | resolved | resolved | tie |

- **Correctness/architecture: a dead tie.** Both arms passed every shape and **both injections** — the
  formal arm's integration mission produced no recovery or correctness advantage here.
- **Cost: a tie.** (Manager tokens; worker-side token cost was not metered — a limitation.)
- **Quality: a tie**, 5–5–1, blinded and randomized. Wins tracked whichever solution happened to be
  more idiomatic/type-safe on that instance, not the arm.
- **Latency: no consistent winner.** The formal arm was ~2× slower on `shared_contract` (its
  retry + compile-probe + longer contract briefs), while the emergent arm had a single large outlier
  on the capability-boundary injection. Means are close; neither arm is systematically faster.

## Conclusion
By the pre-registered rule — adopt a formal mechanism only if it causes no drop in gate-pass rate **and**
wins on judged quality **or** on cost/latency at equal quality, with ties defaulting to the simpler
system — **the evidence does not support adopting formal runtime-managed orchestration as a blanket
mechanism.** A strong supervisor decomposing work and integrating it directly matched the formal
pipeline on correctness, cost, and quality, with less machinery and (on some shapes) less overhead.

Practical read for a roadmap:
- **Keep the cheap plumbing** — worktree isolation, provenance traces, budgets, scoped briefs — as
  ordinary engineering. It's low-risk and useful regardless.
- **Don't build** generic semantic merge operators, rigid isolation contracts, or deep agent
  hierarchies on the strength of "it feels more principled." This benchmark gives no evidence they beat
  a capable supervisor on these tasks.

## What would change the verdict (and the honest limits)
This is a **mechanism test, not a leaderboard**, and it is small:
- **N is small** (11 paired) and the Kotlin tasks are toy-scale, not large multi-day refactors.
- **The injections were too easily recovered** — both arms fixed both, so the tasks may not stress the
  formal arm's hypothesized recovery edge. Harder, deeper-dependency tasks are the obvious next test.
- **One judge family** (blinded, writes no code) and **one manager model**.
- **Worker token cost wasn't metered** (manager tokens + worker latency only).

If formal orchestration has an advantage, it most likely shows on tasks bigger and more interdependent
than these — that's where we'd point a follow-up. Until then, the simpler system wins by default.

## Reproduce
Everything is in this repo. `python3 validate_gates.py` re-checks the gates against reference solutions
with **no API keys and no model calls**; `harness/bench.py <shape> <seed> [injection]` runs a paired
trial (keys via environment variables only). Raw per-run results are in `results/full_matrix.json`; the
aggregate + per-pair judge verdicts are in `results/final_report.json`.
