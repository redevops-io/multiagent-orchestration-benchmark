# Runtime-adversarial lane

The [governed-properties suite](../governed/) run against **pinned** revisions of the runtime, with a run manifest and an honest map from the plan's test families to what is actually tested. It checks that the
ReDevOps runtime (agentic-os, with agent-harness and context-runtime) keeps its guarantees when things go wrong: authority, recovery from unknown outcomes, autonomy limits, context integrity. It is scored on its own
and reports **no score**: counts per family, and a list of what is not tested.

## What this adds to the suite

| | |
|---|---|
| [`PINS.json`](PINS.json) | The exact commits of `agentic-os`, `agent-harness` and `context-runtime` the lane runs against. `governed.yml` runs the same suite against the default branches to catch drift; these pins make a lane result name what produced it. Bump them in a pull request after reading what changed. |
| [`run_lane.py`](run_lane.py) | Refuses a checkout that is not the pinned revision (unless `--allow-unpinned`, which the report records), runs the suite, writes a run manifest in the shape every lane publishes, and evaluates the coverage map. A scenario that was skipped or could not run is `not_tested`; with `--require-all` (CI) that fails the run. |
| [`coverage.json`](coverage.json) | The 41 items of the plan's runtime-adversarial families (authority, recovery, autonomy, context, infrastructure), each mapped to the governed scenarios and to tests in the pinned `agentic-os` that are named for it, with a note wherever the mapping is partial. |
| [`controls.json`](controls.json) | What each scenario provides toward the plan's per-case requirements (positive control, negative control in CI, a defect-seeded revision in CI, a false-denial rate, traces and policy decisions in the results, independently counted effects), stated from what its code checks; for the four scenarios that count false denials, what "legitimate" means there. |
| [`test_runtime_adversarial.py`](test_runtime_adversarial.py) | Contract tests, run on every pull request without any runtime checkout. |
| [`../.github/workflows/runtime-adversarial.yml`](../.github/workflows/runtime-adversarial.yml) | Pull request (when the suite or lane changes), nightly and manual: checks out the three repositories at their pinned commits and runs the lane. |

## How the coverage map is evaluated

A status is **computed**, never typed. For each item:

* `has_governed_scenario`: a scenario mapped to it exists, ran, and had no violation;
* `failing`: a mapped scenario had a violation;
* `has_infra_check`: every infrastructure check mapped to it (`infra.py`: a docker network, an image scan, a log file) ran and passed; a check that could not run (no Docker daemon) leaves the item `not_tested`;
* `has_named_test`: no passing governed scenario, but a test in the pinned `agentic-os` is named for it (the file exists and a `def test_*` in it contains the name);
* `not_tested`: neither. A scenario that was skipped or errored does not count, and a reference that does not resolve is reported as a problem and counts for nothing.

The mappings were written by the lane's author and are open to review by the owner of each area. A named test shows that something is called that; it does not show the test is adequate. `has_governed_scenario`
means the scenario that is mapped passed, not that it exercises every aspect of the item: the `note` on an item says where it does not (for example, the fork scenario uses an ALL join only).

## What is not covered

Listed in every report under `not_built`, and as `not_tested` items. The four categories that were `not_tested` are now executable:

| Item | What runs | What it does not show |
|---|---|---|
| A07 cross-model delegation | `cross_model_delegation` (S8): model-labelled children and grandchildren; forged, re-signed, swapped, truncated, wrong-client, wrong-tenant, closed and superseded credentials; `agent:` identities with no credential; claims in the request; spawns beyond the parent. Oracle: an entitlement predicate from the registry and envelopes, and the provider's own effect log | No real model is called. A credential left out entirely falls back to the app principal's authority: a deployment boundary, recorded not asserted |
| C04 retrieval ACLs | `retrieval_isolation` (S9) over redevops-rag's namespace guard, `document_ids` scoping (semantic, BM25, hybrid) and folder expansion, checking the text handed to a model; `rag_indexer_identity` (S10) over agentic-os's per-(project, source) index files | redevops-rag has no per-document ACLs; the caller scopes. What an unguarded shared store returns is reported in `meta`, not asserted |
| I02 container and network policy | `infra.py network_isolation`: a throwaway `--internal` docker network, the repository's own egress image, an origin whose own log is the oracle, a positive control on the outside network. Kubernetes manifest checked statically | Enforcement of the NetworkPolicy by a cluster is not tested |
| I04 image pinning / I05 tamper-evident logs | `infra.py image_pinning` (fixture-checked classifier; the repository's mutable references are open findings that may only shrink, `image_pin_baseline.json`); `infra.py log_chain` (edit, delete, reorder, insert, replay, hash-stripping detected by the chain alone) | A digest's existence in a registry is not checked. Tail truncation, an appended valid-looking event and a consistent rewrite are invisible to the chain alone and are caught only by an anchored head: listed as known limits |

Limits kept explicit (none of these is closed by a green lane):
* Retrieval ACLs: caller-level scoping is tested; document-level authorization is not.
* Delegation: authority propagation is tested; model-initiated delegation is not.
* Network isolation: Docker isolation is tested; Kubernetes NetworkPolicy enforcement is not.
* Log integrity: hash-chain consistency is tested; externally anchored completeness is not.
* Image pinning: mutable references are detected and the open ones stay tracked findings (51 at the pinned revision, `image_pin_baseline.json`); registry availability and signature verification are separate checks.
* `rag_indexer_identity` is also run against the revision before the fix (`PINS.json` `defective_revisions`) and CI requires it to fail there.

Also not covered: a webhook path; per-document ACLs inside one tenant's store; the enterprise store and extensions (a separate track: the private repositories are not available to public CI); and the expensive multi-model and multi-replica tests the plan puts on a schedule.

Every new check has a defect-seeded variant that CI runs and requires to fail (`GOVERNED_CROSS_MODEL_BUG`, `GOVERNED_RETRIEVAL_BUG`, `GOVERNED_INDEXER_BUG`, `RA_NETWORK_BUG`, `RA_PINNING_BUG`, `RA_LOG_BUG`).

The counters are themselves checked: with `GOVERNED_OVERDENY=1` the guard refuses everything, which is perfectly safe (no safety check fails) and useless; CI requires the false-denial rate to reach at least 90% there, so a counter that could not see over-denial would fail the build.

## Limits

* The scenarios run in one process with simulated crashes (an exception at an event boundary, then a fresh runtime over the same files), not a killed OS process or a second host.
* A pass means no violation was found in the explored space, not that none exists. The oracles are written from the properties the runtime claims; a property nobody wrote down is not checked.
* The lane's own negative controls are the ones `governed.yml` runs against older revisions; scenarios whose `negative_control_in_ci` is false have no check that the scenario would fail on a defective runtime.
