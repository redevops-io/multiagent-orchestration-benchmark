# Governed-properties suite

The benchmark in the rest of this repository compares two orchestration mechanisms on Kotlin tasks. It does not measure what a governed runtime guarantees: authority,
approval, replay, reconciliation of unknown outcomes. This suite checks some of those guarantees for the ReDevOps runtime
([`agentic-os`](https://github.com/redevops-io/agentic-os)). It is separate from the experiment: it does not touch `spec/`, `harness/` or the frozen results.

Each scenario states what must **always** hold (the oracle), drives the real `agentic_os` components with seeded faults, and counts checks and violations. These are
mechanism checks, not a comparison of strategies and not performance measurements. Offline: no model calls, no API keys, no network.

| Scenario | What it drives | What must always hold |
|---|---|---|
| `fork_crash_restart` | A three-branch fork (a success, an UNKNOWN outcome, a read-only call) killed at every event boundary, before and after the event is written, then restarted and reconciled | A side-effecting call is never sent again after its effect happened; no effect happens twice; the fork reaches the one correct close once; no reservation is left open |
| `goal_loop_chaos` | A goal loop over capabilities that succeed, are rejected, or time out (effect applied or not), with replans, escalations and ceilings | Never above the iteration ceiling; no resend after an effect; no new iteration or replan while an outcome is unresolved; completes only with a verified goal; ends in a state a person can act on; its log replays to the same state |
| `child_agent_tree` | Random spawn, spend, close, revoke, narrow and clock steps over a tree of delegated agents | No agent holds more than its parent grants; nothing is spent beyond the root's budget or an agent's allowance; an agent under a revoked, narrowed or expired ancestor is denied; a credential verifies only for an active agent whose driver is alive |
| `compaction_fuzz` | Random histories with planted look-alike scope blocks, instruction-laden tool results and failing summarizers | The owner's pinned scope is first and intact after every compaction; a summary of untrusted content is labelled and never given the system role; a failing summarizer never costs history |
| `projects_routes` | Every write route of the Projects API in the production profile | An unauthenticated or wrongly authenticated write is refused before any handler runs; a service identity never makes a human decision; the operator credential is accepted |

## Run

```bash
pip install /path/to/agentic-os                    # or: export AGENTIC_OS_PATH=/path/to/agentic-os
python3 governed/run_governed.py                   # all scenarios, default seeds
python3 governed/run_governed.py --only fork_crash_restart --seeds 12
```

Writes `results/governed_properties.json` (per scenario: seeds, checks, violations, the first violations, the `agentic-os` revision) and exits 1 on any violation, 2 if a scenario
could not run. A scenario that cannot run is reported as an error, never counted as a pass.

## Does the suite detect anything?

A suite that only ever passes proves little, so CI also runs a scenario against an `agentic-os` revision that predates the behaviour and requires it to **fail**:
`fork_crash_restart` against the commit before fork reconciliation parity (`05dd311`, full SHA in the workflow). Against that commit it reports hundreds of violations (an unknown outcome failed the branch and
left a reservation open); against current `agentic-os` it reports none.

## Limits

* The oracles are written from the properties the runtime claims; they are only as good as those statements. A property nobody wrote down is not checked.
* Faults are seeded and bounded. A pass means no violation was found in the explored space, not that none exists.
* The scenarios run in one process with simulated crashes (an exception at an event boundary, then a fresh runtime over the same files), not a killed OS process or a second host.
* `compaction_fuzz` checks that compaction cannot give untrusted content the system role or displace the owner's scope. It does not detect prompt injection and does not check that a
  summary is faithful.
* Messages a caller already placed in the system role are the caller's; `demote_untrusted_system_roles` is the helper for those and the suite checks it separately.
