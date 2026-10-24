#!/usr/bin/env python3
"""Run the governed-properties suite and write `results/governed_properties.json`.

    python3 governed/run_governed.py [--agentic-os PATH] [--seeds N] [--only NAME ...] [--out FILE]

Offline: no model calls, no API keys. Needs an `agentic_os` checkout (`--agentic-os` or $AGENTIC_OS_PATH) or an installed package. Exits 1 if any
invariant is violated, 2 if a scenario could not run."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentic-os", default=None)
    ap.add_argument("--agent-harness", default=None, help="path to an agent-harness checkout (or $AGENT_HARNESS_PATH, or installed)")
    ap.add_argument("--context-runtime", default=None, help="path to a context-runtime checkout (or $CONTEXT_RUNTIME_PATH, or installed)")
    ap.add_argument("--seeds", type=int, default=None, help="override the number of seeds of every randomized scenario")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", default=str(ROOT / "results" / "governed_properties.json"))
    a = ap.parse_args()
    os.environ["AGENTIC_OS_PROFILE"] = "production"                  # the properties are claimed for the production profile
    os.environ.pop("AGENTIC_OS_APPROVAL_SECRET", None)
    from governed import kit
    rev = kit.import_agentic_os(a.agentic_os)
    stack = kit.import_agent_stack(a.agent_harness, a.context_runtime)
    if os.environ.get("GOVERNED_OVERDENY") == "1":
        # negative control for the false-denial counters: a guard that refuses EVERYTHING is perfectly safe (no scenario can find an unauthorized effect) and useless. The safety checks stay green;
        # the false-denial rate is what must catch it.
        from agentic_os.mission import authority as _auth

        def _deny_all(self, mission_id, tenant, action, standing=None, lease_id="", lease_meta=None):
            return _auth.Decision(_auth.Verdict.DENY, "overdeny_control", "the over-denying control refuses everything")
        _auth.AuthorityGuard.authorize = _deny_all
    from governed import scenarios
    report = {"agentic_os": rev, "agent_harness": stack["agent_harness"], "context_runtime": stack["context_runtime"], "python": sys.version.split()[0], "profile": "production", "scenarios": {}}
    rc = 0
    for name, (fn, default_seeds) in scenarios.SCENARIOS.items():
        if a.only and name not in a.only:
            continue
        n = default_seeds if (a.seeds is None or default_seeds == 1) else a.seeds
        t0 = time.time()
        checks, violations, metas, error = 0, [], [], None
        legit_total, legit_refused = 0, []
        with tempfile.TemporaryDirectory() as tmp:
            for seed in range(n):
                try:
                    r = fn(seed, Path(tmp))
                except Exception:  # noqa: BLE001 - a scenario that cannot run is reported, never counted as passing
                    error = f"seed {seed}: {traceback.format_exc(limit=3)}"
                    break
                checks += r.checks
                legit_total += r.legit_total
                legit_refused += [f"seed {seed}: {v}" for v in r.legit_refused]
                violations += [f"seed {seed}: {v}" for v in r.violations]
                metas.append(r.meta)
        entry = {"seeds": n, "checks": checks, "violations": len(violations), "first_violations": violations[:10],
                 "legitimate_actions": legit_total, "legitimate_refused": len(legit_refused),
                 "false_denial_rate": (round(len(legit_refused) / legit_total, 4) if legit_total else None), "first_false_denials": legit_refused[:5],
                 "seconds": round(time.time() - t0, 1), "sample_meta": metas[0] if metas else {}}
        if error:
            entry["error"] = error
            rc = max(rc, 2)
        elif violations:
            rc = max(rc, 1)
        if metas and all(isinstance(m, dict) and m.get("skipped") for m in metas):
            entry["skipped"] = metas[0]["skipped"]
        report["scenarios"][name] = entry
        status = "SKIPPED" if entry.get("skipped") else "ERROR" if error else ("FAIL" if violations else "ok")
        fd = f"  false denials {len(legit_refused)}/{legit_total}" if legit_total else ""
        print(f"{name:22s} {status:8s} seeds={n:<3d} checks={checks:<6d} violations={len(violations)}{fd}  ({entry['seconds']}s)")
        for v in violations[:3]:
            print("   ", v)
        if error:
            print("   ", error.strip().splitlines()[-1])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {a.out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
