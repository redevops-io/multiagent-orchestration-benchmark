# Runtime-adversarial lane

The [governed-properties suite](../governed/) run against **pinned** revisions of the runtime, with a run manifest and an honest map from the plan's test families to what is actually tested. It checks that the
ReDevOps runtime (agentic-os, with agent-harness and context-runtime) keeps its guarantees when things go wrong: authority, recovery from unknown outcomes, autonomy limits, context integrity. It is scored on its own
and reports **no score**: counts per family, and a list of what is not tested.

## What this adds to the suite

| | |
|---|---|
| [`PINS.json`](PINS.json) | The exact commits of `agentic-os`, `agent-harness` and `context-runtime` the lane runs against. `governed.yml` runs the same suite against the default branches to catch drift; these pins make a lane result name what produced it. Bump them in a pull request after reading what changed. |
| [`run_lane.py`](run_lane.py) | Refuses a checkout that is not the pinned revision (unless `--allow-unpinned`, which the report records), runs the suite, writes a run manifest in the shape every lane publishes, and evaluates the coverage map. A scenario that was skipped or could not run is `not_tested`; with `--require-all` (CI) that fails the run. |
| [`coverage.json`](coverage.json) | The 40 items of the plan's runtime-adversarial families (authority, recovery, autonomy, context, infrastructure), each mapped to the governed scenarios and to tests in the pinned `agentic-os` that are named for it, with a note wherever the mapping is partial. |
| [`controls.json`](controls.json) | What each scenario provides toward the plan's per-case requirements (positive control, negative control in CI, a defect-seeded revision in CI, a false-denial rate, traces and policy decisions in the results, independently counted effects), stated from what its code checks; for the four scenarios that count false denials, what "legitimate" means there. |
| [`test_runtime_adversarial.py`](test_runtime_adversarial.py) | Contract tests, run on every pull request without any runtime checkout. |
| [`../.github/workflows/runtime-adversarial.yml`](../.github/workflows/runtime-adversarial.yml) | Pull request (when the suite or lane changes), nightly and manual: checks out the three repositories at their pinned commits and runs the lane. |

## How the coverage map is evaluated

A status is **computed**, never typed. For each item:

* `has_governed_scenario`: a scenario mapped to it exists, ran, and had no violation;
* `failing`: a mapped scenario had a violation;
* `has_named_test`: no passing governed scenario, but a test in the pinned `agentic-os` is named for it (the file exists and a `def test_*` in it contains the name);
* `not_tested`: neither. A scenario that was skipped or errored does not count, and a reference that does not resolve is reported as a problem and counts for nothing.

The mappings were written by the lane's author and are open to review by the owner of each area. A named test shows that something is called that; it does not show the test is adequate. `has_governed_scenario`
means the scenario that is mapped passed, not that it exercises every aspect of the item: the `note` on an item says where it does not (for example, the fork scenario uses an ALL join only).

## What is not covered

Listed in every report under `not_built`, and as `not_tested` items: cross-model delegation; a webhook path; container and network-policy tests (the egress and sandbox checks exercise Python components, not a network
namespace); image pinning and log-chain checks; retrieval ACLs (they belong to the context-runtime and RAG repositories); and the expensive multi-model and multi-replica tests the plan puts on a schedule. **The false-denial rate is measured in four scenarios only** (`child_agent_tree`, `projects_routes`, `agent_in_the_loop`, `provider_reconciliation`): each counts the actions that a predicate written by the benchmark, without the system's policy code, calls legitimate, and how many of them the system refused (the predicates are in `controls.json`). The others are reported as `not measured`, never as zero. A rate of zero over a small number of actions is not evidence that governance never gets in the way: the denominators are in the report, and the predicates can be wrong in either direction, which is why the first refusals are kept with their reasons. Traces and policy decisions are not stored in the results file.

The counters are themselves checked: with `GOVERNED_OVERDENY=1` the guard refuses everything, which is perfectly safe (no safety check fails) and useless; CI requires the false-denial rate to reach at least 90% there, so a counter that could not see over-denial would fail the build.

## Limits

* The scenarios run in one process with simulated crashes (an exception at an event boundary, then a fresh runtime over the same files), not a killed OS process or a second host.
* A pass means no violation was found in the explored space, not that none exists. The oracles are written from the properties the runtime claims; a property nobody wrote down is not checked.
* The lane's own negative controls are the ones `governed.yml` runs against older revisions; scenarios whose `negative_control_in_ci` is false have no check that the scenario would fail on a defective runtime.
