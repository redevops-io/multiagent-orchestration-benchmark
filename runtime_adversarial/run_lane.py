#!/usr/bin/env python3
"""The runtime-adversarial lane: the governed-properties suite run against PINNED revisions, with a run manifest and an honest coverage report of the plan's test families.

    python3 runtime_adversarial/run_lane.py --agentic-os PATH --agent-harness PATH --context-runtime PATH [--seeds N] [--out FILE] [--allow-unpinned]

It does three things the suite alone does not:
  1. Refuses to run against a checkout that is not the pinned revision (PINS.json), unless told to, and records what it ran against.
  2. Writes a run manifest in the common shape every lane publishes (suite, revisions, seed, runner, times, evidence digests, status, ...). A scenario that was skipped or could not run is `not_tested`.
  3. Maps each line of the plan's runtime-adversarial families (coverage.json) to what actually exists: a governed scenario that passed, a test named for it in the pinned agentic-os, or nothing. Every reference
     is checked (the scenario is registered, the named test exists in the checkout). The result is three counts per family, never a single score.
  4. Reports the FALSE-DENIAL rate where a scenario counts it: of the actions an oracle-side predicate says were legitimate, how many the system refused (controls.json says what the predicate is, per scenario).
     A scenario that does not count legitimate actions is reported as not measured, never as zero.

Offline, deterministic, no model calls. Exit 1 on any scenario violation, 2 if a scenario could not run (or was skipped with --require-all), 3 if a checkout is not the pinned revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

EVALUATOR_VERSION = "1"
FAMILIES = ("authority", "recovery", "autonomy", "context", "infrastructure")
REQUIRED_MANIFEST = ("suite", "run_id", "source_revisions", "model_ids", "provider", "budget", "seed", "runner_image_digest", "start", "end", "result_uri", "evidence_digests",
                     "evaluator_version", "environment", "status")


def git_head(path: "str | None") -> str:
    if not path:
        return ""
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def named_test_exists(checkout: "str | None", ref: dict) -> bool:
    """The file exists in the checkout and a `def test_*` in it contains the name."""
    if not checkout:
        return False
    f = Path(checkout) / ref["file"]
    try:
        text = f.read_text(errors="replace")
    except OSError:
        return False
    return any(ref["name"] in m for m in re.findall(r"^\s*def (test_\w+)", text, re.M))


def evaluate_coverage(coverage: dict, scenario_results: dict, registered: set, agentic_os: "str | None") -> dict:
    """Per item: has_governed_scenario / failing / has_named_test / not_tested, with the reason. References that do not resolve are reported and never count."""
    out, problems = [], []
    for it in coverage["items"]:
        gov_ok, gov_bad, gov_untested, missing = [], [], [], []
        for name in it["governed"]:
            if name not in registered:
                problems.append(f"{it['id']}: governed scenario {name!r} is not registered")
                continue
            r = scenario_results.get(name)
            if r is None or r.get("skipped") or r.get("error"):
                gov_untested.append(name)
            elif r.get("violations", 0) > 0:
                gov_bad.append(name)
            else:
                gov_ok.append(name)
        tests_ok = [t for t in it["source_tests"] if named_test_exists(agentic_os, t)]
        tests_missing = [t for t in it["source_tests"] if not named_test_exists(agentic_os, t)]
        if tests_missing:
            problems.append(f"{it['id']}: named test(s) not found in the checkout: " + "; ".join(f"{t['file']}::{t['name']}" for t in tests_missing))
        if gov_bad:
            status = "failing"
        elif gov_ok:
            status = "has_governed_scenario"
        elif tests_ok:
            status = "has_named_test"
        else:
            status = "not_tested"
        out.append({"id": it["id"], "family": it["family"], "item": it["item"], "status": status, "governed_passed": gov_ok, "governed_failing": gov_bad, "governed_not_run": gov_untested,
                    "named_tests_found": [f"{t['file']}::{t['name']}" for t in tests_ok], "note": it.get("note", "")})
    fam = {f: {"items": 0, "has_governed_scenario": 0, "has_named_test": 0, "not_tested": 0, "failing": 0} for f in FAMILIES}
    for o in out:
        fam[o["family"]]["items"] += 1
        fam[o["family"]][o["status"]] += 1
    return {"items": out, "by_family": fam, "reference_problems": problems,
            "note": "Counts per family; no score is computed across items or families. `has_named_test` means a test is named for the item in the pinned agentic-os, not that it is adequate."}


def run_suite(args, out_json: Path) -> int:
    cmd = [sys.executable, str(ROOT / "governed" / "run_governed.py"), "--seeds", str(args.seeds), "--out", str(out_json)]
    for flag, val in (("--agentic-os", args.agentic_os), ("--agent-harness", args.agent_harness), ("--context-runtime", args.context_runtime)):
        if val:
            cmd += [flag, val]
    env = dict(os.environ)
    env.pop("AGENTIC_OS_APPROVAL_SECRET", None)
    return subprocess.run(cmd, env=env).returncode


def build_manifest(pins: dict, heads: dict, seeds: int, rc: int, start: str, end: str, digest: str, result_uri: str, results: dict, require_all: bool) -> dict:
    scen = results.get("scenarios", {})
    skipped = sorted(n for n, r in scen.items() if r.get("skipped"))
    errored = sorted(n for n, r in scen.items() if r.get("error"))
    status = "completed" if rc == 0 and not skipped and not errored else "partial" if rc in (0, 1) else "infrastructure_failure"
    return {"suite": "runtime-adversarial", "run_id": f"governed-{int(time.time())}", "source_revisions": heads, "pinned_revisions": {k: v["revision"] for k, v in pins["sources"].items()},
            "model_ids": ["none: scripted, deterministic fake providers"], "provider": "simulated", "budget": {"model_cost_usd": 0, "seeds": seeds, "note": "no model or provider is called"}, "seed": f"seeds 0..{seeds - 1}",
            "runner_image_digest": os.environ.get("RUNNER_IMAGE_DIGEST", "not recorded: runner image digest is not exposed to the job"), "start": start, "end": end, "result_uri": result_uri,
            "evidence_digests": {"governed_properties.json": digest}, "evaluator_version": EVALUATOR_VERSION,
            "environment": {"python": platform.python_version(), "platform": platform.platform(), "profile": results.get("profile")}, "status": status,
            "not_tested": {"skipped_scenarios": skipped, "errored_scenarios": errored}, "require_all": require_all}


def false_denials(scen: dict) -> dict:
    """The false-denial rate per scenario from the counters the suite recorded. No counter (or no legitimate action seen) is `not measured`, never 0."""
    out = {}
    for n in sorted(scen):
        r = scen[n]
        total, refused = r.get("legitimate_actions"), r.get("legitimate_refused")
        if r.get("skipped") or r.get("error") or not total:
            out[n] = "not measured"
        else:
            out[n] = {"legitimate_actions": total, "refused": refused, "rate": round(refused / total, 4), "first_refusals": list(r.get("first_false_denials") or [])[:3]}
    return out


def manifest_problems(m: dict) -> list:
    return [f"missing {k}" for k in REQUIRED_MANIFEST if k not in m or m[k] in (None, "", [], {})]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentic-os", required=True)
    ap.add_argument("--agent-harness")
    ap.add_argument("--context-runtime")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--out", default=str(ROOT / "results" / "runtime_adversarial.json"))
    ap.add_argument("--allow-unpinned", action="store_true", help="run against checkouts that are not the pinned revisions (the report says so)")
    ap.add_argument("--require-all", action="store_true", help="a skipped scenario fails the run (CI uses this)")
    ap.add_argument("--governed-results", help="use an existing governed_properties.json instead of running the suite (tests)")
    a = ap.parse_args()
    pins = json.loads((HERE / "PINS.json").read_text())
    a.seeds = a.seeds or pins["seeds"]
    heads = {"agentic-os": git_head(a.agentic_os), "agent-harness": git_head(a.agent_harness), "context-runtime": git_head(a.context_runtime)}
    mismatched = {k: {"pinned": pins["sources"][k]["revision"], "found": v or "unknown"} for k, v in heads.items() if v != pins["sources"][k]["revision"]
                  and (k == "agentic-os" or (k == "agent-harness" and a.agent_harness) or (k == "context-runtime" and a.context_runtime))}
    if mismatched and not a.allow_unpinned:
        print("not the pinned revision:", json.dumps(mismatched, indent=2))
        return 3
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with tempfile.TemporaryDirectory() as td:
        gp = Path(a.governed_results) if a.governed_results else Path(td) / "governed.json"
        rc = 0 if a.governed_results else run_suite(a, gp)
        if not gp.exists():
            print("the suite wrote no results: the run is not_tested")
            return 2
        results = json.loads(gp.read_text())
        digest = hashlib.sha256(gp.read_bytes()).hexdigest()
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    from governed import scenarios as S             # imports only the standard library at module level
    registered = set(S.SCENARIOS)
    scen = results.get("scenarios", {})
    controls = json.loads((HERE / "controls.json").read_text())
    cov = evaluate_coverage(json.loads((HERE / "coverage.json").read_text()), scen, registered, a.agentic_os)
    manifest = build_manifest(pins, heads, a.seeds, rc, start, end, digest, f"file:{Path(a.out).name}", results, a.require_all)
    manifest["pins_match"] = not mismatched
    if mismatched:
        manifest["unpinned"] = mismatched
    report = {"lane": "runtime_adversarial", "manifest": manifest, "manifest_problems": manifest_problems(manifest),
              "scenarios": {n: {k: scen[n].get(k) for k in ("seeds", "checks", "violations", "skipped", "error", "seconds")} for n in sorted(scen)},
              "false_denials": {"by_scenario": false_denials(scen), "note": "a refusal of an action the oracle's predicate calls legitimate; a rate to read, not a violation; per-scenario definitions are in controls.json"},
              "scenario_controls": {n: controls["scenarios"].get(n, "not declared") for n in sorted(scen)}, "controls_note": controls["note"],
              "coverage": cov,
              "not_built": ["multi-model and multi-replica scheduled tests", "container and network-policy tests", "image pinning and log-chain checks", "retrieval ACL tests (they belong to the context-runtime and RAG repositories)",
                            "false-denial counting outside the four scenarios that have it (see false_denials)", "traces and policy decisions in the results file"],
              "no_aggregate": "no score is combined across scenarios, families or lanes"}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    viol = sum(r.get("violations", 0) for r in scen.values())
    skipped = [n for n, r in scen.items() if r.get("skipped")]
    errored = [n for n, r in scen.items() if r.get("error")]
    print(f"runtime-adversarial: {len(scen)} scenarios, {viol} violation(s), skipped {skipped or 'none'}, errored {errored or 'none'}; manifest status {manifest['status']}")
    for fam, c in cov["by_family"].items():
        print(f"  {fam:15} items={c['items']:<3} governed={c['has_governed_scenario']:<3} named_test_only={c['has_named_test']:<3} not_tested={c['not_tested']:<3} failing={c['failing']}")
    for p in cov["reference_problems"]:
        print("  reference problem:", p)
    if errored or (a.require_all and skipped) or cov["reference_problems"] and a.require_all:
        return 2
    return 1 if viol or rc == 1 else (2 if rc else 0)


if __name__ == "__main__":
    raise SystemExit(main())
