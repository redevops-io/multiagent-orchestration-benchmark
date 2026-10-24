# SOC 2 assessment-accuracy lane

How often does Edge Sentinel's SOC 2 readiness assessment reach a conclusion that its evidence does not support? This lane measures that, with synthetic fixtures whose truth is written down when
the fixture is built. It measures the **assessment**: it says nothing about any organisation's compliance, and nothing here claims that a control, a criterion or an export satisfies SOC 2.

It is scored on its own. It is not combined with the orchestration experiment, the governed-properties suite or any other lane, and it reports **no aggregate score**: averaging a false PASS
into a count of cautious answers would hide the one error that matters.

## What is checked

Every case is built from a design that states what is true (how many privileged accounts lack MFA, whether a restore test ran, whether stored evidence was altered afterwards). The oracle
([`oracle.py`](oracle.py)) maps a truth label to the results that are safe for it and does not run or re-implement any of Edge Sentinel's procedures.

| Truth | Safe results |
|---|---|
| `COMPLIANT` (sound evidence, criterion met) | `PASS` |
| `NONCOMPLIANT` (sound evidence, criterion not met) | `FAIL` |
| `UNKNOWN` (altered, partial, empty, wrong scope or target, unreadable, missing) | `INCONCLUSIVE`, `NOT_TESTED` |
| `MANUAL` (needs a person) | `MANUAL_REVIEW` |
| `NA_DECLARED` (a named person declared it not applicable) | `NOT_APPLICABLE` |

**Gates** (one occurrence fails the run): `false_pass` (a `PASS` on anything but compliant evidence), `unsupported_fail` (a `FAIL` the evidence does not support),
`unfounded_not_applicable`, `forged_evidence_accepted` (the vault accepted evidence with a bad signature, an unknown key, a tenant the key may not write for, or future coverage),
`cross_tenant_leak`, `nondeterministic` (the same evidence, a different result), `claim_in_export` (the exported pack or summary carries a certification, a score or verdict language) and
`export_leak` (raw evidence content in an export).

**Reported, not gates:** `missed_detection` (a sound violation that ended inconclusive), `abstained_on_clean` (sound, compliant evidence that did not end in a `PASS`), and the result counts per
fault family and per control, so a reader can see where the assessor abstains.

Fault families: `producer_claim` (the producer says compliant while the data shows otherwise), `coverage_gap`, `stale`, `wrong_scope`, `wrong_tenant`, `forged`, `malformed`, `tamper` (content or
header changed on disk by someone with the file, the append-only triggers removed), `incomplete`, `empty`, `wrong_target`, `replay`, `self_attest`, `expired`, `wrong_control`, `inferred_na`, and the
plain cases where the data decides (`none`).

## Run

```bash
python3 soc2_accuracy/run_soc2.py --edge-sentinel /path/to/agentic-os --seeds 20     # or $EDGE_SENTINEL_PATH
python3 soc2_accuracy/run_soc2.py --edge-sentinel /path/to/agentic-os --control naive   # the oracle must FAIL against a naive assessor
```

Writes `results/soc2_assessment_accuracy.json` and exits 1 on any gate violation. Offline; synthetic tenants, accounts and secrets only; no model calls. The `soc2` package is loaded by path:
the Edge Sentinel directory holds an `operator.py` that would shadow the standard library module if it were put on `sys.path`.

## Does the lane detect anything?

CI also runs it twice to require failure: against a naive assessor that believes the producer's own verdict (hundreds of false passes), and against the `agentic-os` revision before the first
finding was fixed.

**First finding.** SEC-CFG-01 (host baseline scan) ignored a producer's explicit `population_complete: false` and ended `PASS`; the other procedures honoured it. It was fixed in `agentic-os`
(the revision before the fix is pinned in the workflow); with the fix, 16,600 cases (100 seeds) produce no gate violation.

## Limits

* The fixtures are written by the same people who wrote the assessor. Truth labels were reviewed against what the data means, not what the assessor returns; one label was corrected after the first
  run (a complete population of no backups and no restore tests shows a violation; it is not unreadable evidence). A fault nobody thought of is not covered.
* A clean run means no gate was violated in the cases generated, not that none exists. Seeds vary sizes and which item in a population is wrong, not the set of recipes.
* Real collectors, a real vault deployment and real evidence formats are not exercised; the evidence shapes are the ones the controls document.
* Completeness of the *set* of things in scope (every host, every account) is whatever a collector says and signs; the lane checks that a producer's own statement of incompleteness is believed, not
  that the collector found everything.
* Edge Sentinel's control catalog maps controls to AICPA criteria as a hypothesis for a reviewer; this lane does not validate those mappings.
