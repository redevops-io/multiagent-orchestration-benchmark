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
    from governed import scenarios
    report = {"agentic_os": rev, "agent_harness": stack["agent_harness"], "context_runtime": stack["context_runtime"], "python": sys.version.split()[0], "profile": "production", "scenarios": {}}
    rc = 0
    for name, (fn, default_seeds) in scenarios.SCENARIOS.items():
        if a.only and name not in a.only:
            continue
        n = default_seeds if (a.seeds is None or default_seeds == 1) else a.seeds
        t0 = time.time()
        checks, violations, metas, error = 0, [], [], None
        with tempfile.TemporaryDirectory() as tmp:
            for seed in range(n):
                try:
                    r = fn(seed, Path(tmp))
                except Exception:  # noqa: BLE001 - a scenario that cannot run is reported, never counted as passing
                    error = f"seed {seed}: {traceback.format_exc(limit=3)}"
                    break
                checks += r.checks
                violations += [f"seed {seed}: {v}" for v in r.violations]
                metas.append(r.meta)
        entry = {"seeds": n, "checks": checks, "violations": len(violations), "first_violations": violations[:10],
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
        print(f"{name:22s} {status:8s} seeds={n:<3d} checks={checks:<6d} violations={len(violations)}  ({entry['seconds']}s)")
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
