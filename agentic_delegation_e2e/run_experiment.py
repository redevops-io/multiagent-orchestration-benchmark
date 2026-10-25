#!/usr/bin/env python3
"""Runs the delegation experiment in isolated containers and evaluates each run from the provider's own database.

    python agentic_delegation_e2e/run_experiment.py --agentic-os PATH --llm-base-url http://HOST:PORT/v1 --llm-model NAME [--arms baseline-subset,baseline-free,governed] [--trials N]
                                                    [--model-kind llm|scripted] [--defect none|weak_authority] [--out DIR]

One run = one fresh set of containers on a throwaway INTERNAL docker network: the provider (the backend, with its own database), an egress proxy whose allowlist is the model endpoint and nothing else, and the runner (the agents). The runner has no route out except the proxy;
the provider has none at all. After the run the provider is stopped and its database copied out; the evaluator judges from that file. Image IDs, the base digest, the pinned agentic-os revision, the prompts' hashes, the scenario's hash and the egress decisions are recorded.

This is our own experiment, NOT the upstream CyberGym-E2E benchmark (see ../cybergym_e2e/ for that), and nothing it produces is a CyberGym result. A run with `--model-kind scripted` validates the harness and is never a model result."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agent as A       # noqa: E402
import evaluate as E    # noqa: E402

ARMS = {"baseline-subset": ("baseline", ["--baseline-inheritance", "subset"]), "baseline-free": ("baseline", ["--baseline-inheritance", "free"]), "governed": ("governed", [])}


def sh(*args, timeout=900, check=False, input=None):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=check, input=input)


def head(path: str) -> str:
    return sh("git", "-C", path, "rev-parse", "HEAD").stdout.strip() or "unknown"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_images(agentic_os: str, tag: str) -> dict:
    ctx = Path(tempfile.mkdtemp())
    try:
        (ctx / "agentic-os").mkdir()
        arc = subprocess.run(["git", "-C", agentic_os, "archive", "HEAD"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(ctx / "agentic-os")], input=arc, check=True)
        for f in ("agent.py", "provider.py", "scenario.json", "scenario-2.json", "Dockerfile"):
            shutil.copy(HERE / f, ctx / f)
        b = sh("docker", "build", "-q", "-t", f"adel-runner:{tag}", str(ctx), timeout=1800)
        if b.returncode != 0:
            raise SystemExit("could not build the runner image:\n" + (b.stderr or b.stdout)[-600:])
        e = sh("docker", "build", "-q", "-t", f"adel-egress:{tag}", str(Path(agentic_os) / "deploy" / "egress"), timeout=900)
        if e.returncode != 0:
            raise SystemExit("could not build the egress image:\n" + (e.stderr or e.stdout)[-600:])
    finally:
        shutil.rmtree(ctx, ignore_errors=True)
    ids = {n: sh("docker", "image", "inspect", "-f", "{{.Id}}", f"adel-{n}:{tag}").stdout.strip() for n in ("runner", "egress")}
    return {"runner": f"adel-runner:{tag}", "egress": f"adel-egress:{tag}", "image_ids": ids, "runner_base": re.search(r"^FROM (\S+)", (HERE / "Dockerfile").read_text(), re.M).group(1), "git_dependencies_note": "some agentic-os dependencies are installed from git tags at build time, which are not digests; the built image ID is the record"}


def one_run(arm_name: str, trial: int, imgs: dict, args, out: Path, scenario: dict) -> dict:
    scen_file = args.scenario
    kind, extra = ARMS[arm_name]
    T = f"adel-{uuid.uuid4().hex[:8]}"
    INT, OUT = f"{T}-int", f"{T}-out"
    names = [f"{T}-proxy", f"{T}-provider", f"{T}-runner"]
    run_dir = out / f"{arm_name}-trial{trial}"
    run_dir.mkdir(parents=True, exist_ok=True)
    allow = run_dir / "allowlist.txt"
    u = urlparse(args.llm_base_url) if args.model_kind == "llm" else None
    rule = ""
    if u:
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        try:
            private = ipaddress.ip_address(host).is_private
        except ValueError:
            private = False
        rule = f"{host}:{port}" + (" private" if private else "") + "\n"
    allow.write_text(rule)
    rec = {"arm": arm_name, "trial": trial, "run_id": T}
    try:
        sh("docker", "network", "create", "--internal", INT, check=True)
        sh("docker", "network", "create", OUT, check=True)
        rec["network_internal"] = sh("docker", "network", "inspect", "-f", "{{.Internal}}", INT).stdout.strip() == "true"
        sh("docker", "run", "-d", "--name", names[0], "--network", INT, "--network-alias", "egress", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-v", f"{allow}:/etc/egress/allowlist.txt:ro", imgs["egress"], check=True)
        sh("docker", "network", "connect", OUT, names[0], check=True)
        sh("docker", "run", "-d", "--name", names[1], "--network", INT, "--network-alias", "provider", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", "python", imgs["runner"],
           "/app/provider.py", "/tmp/p.db", f"/app/{scen_file}", "8090", check=True)
        time.sleep(2.5)
        cmd = ["python", "/app/agent.py", "--arm", kind, "--provider", "http://provider:8090", "--scenario", f"/app/{scen_file}", "--out", "/tmp/out", "--model-kind", args.model_kind, "--trial", str(trial), "--defect", args.defect, "--temperature", str(args.temperature), *extra]
        if args.model_kind == "llm":
            cmd += ["--llm-base-url", args.llm_base_url, "--llm-model", args.llm_model]
        env = ["-e", "HTTP_PROXY=http://egress:3128", "-e", "http_proxy=http://egress:3128", "-e", "NO_PROXY=provider,egress", "-e", "no_proxy=provider,egress"]
        t0 = time.time()
        r = sh("docker", "run", "--name", names[2], "--network", INT, *env, "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--pids-limit", "256", imgs["runner"], *cmd, timeout=args.timeout)
        rec["runner_exit"], rec["wall_seconds"] = r.returncode, round(time.time() - t0, 1)
        (run_dir / "runner.stdout").write_text(r.stdout); (run_dir / "runner.stderr").write_text(r.stderr[-4000:])
        sh("docker", "cp", f"{names[2]}:/tmp/out/.", str(run_dir))
        sh("docker", "stop", names[1])
        c = sh("docker", "cp", f"{names[1]}:/tmp/p.db", str(run_dir / "provider.db"))
        rec["provider_db_copied"] = c.returncode == 0
        plog = sh("docker", "logs", names[0])
        (run_dir / "egress.log").write_text(plog.stdout + plog.stderr)
        dec = [json.loads(x) for x in (plog.stdout + plog.stderr).splitlines() if x.startswith("{") and '"allow"' in x]
        rec["egress"] = {"decisions": len(dec), "allowed_hosts": sorted({f"{d.get('host')}:{d.get('port')}" for d in dec if d.get("allow")}), "refused": [f"{d.get('host')}:{d.get('port')}" for d in dec if not d.get("allow")][:10]}
        rec["isolation_ok"] = bool(rec["network_internal"]) and not rec["egress"]["refused"] and set(rec["egress"]["allowed_hosts"]) <= ({f"{u.hostname}:{u.port or 80}"} if u else set())
        if rec["provider_db_copied"] and rec["runner_exit"] == 0:
            ev = E.evaluate(str(run_dir / "provider.db"), scenario, str(run_dir), arm_name)
            (run_dir / "evaluation.json").write_text(json.dumps(ev, indent=1))
            rec["evaluation"] = ev
        else:
            rec["error"] = "the run did not complete: " + (r.stderr or r.stdout)[-300:]
    finally:
        for n in names:
            sh("docker", "rm", "-f", n)
        for n in (INT, OUT):
            sh("docker", "network", "rm", n)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentic-os", required=True)
    ap.add_argument("--llm-base-url", default="")
    ap.add_argument("--llm-model", default="")
    ap.add_argument("--model-kind", choices=["llm", "scripted"], default="llm")
    ap.add_argument("--arms", default="baseline-subset,governed")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--defect", choices=["none", "weak_authority"], default="none")
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--cells", default="", help="run only these arm:trial cells, e.g. baseline-subset:0,governed:2 (a continuation of an earlier run; the earlier failures stay on record)")
    ap.add_argument("--scenario", default="scenario.json", choices=["scenario.json", "scenario-2.json"])
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--out", default=str(HERE / "runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
    args = ap.parse_args()
    if args.model_kind == "llm" and not (args.llm_base_url and args.llm_model):
        print("an llm run needs --llm-base-url and --llm-model"); return 2
    if sh("docker", "info", "--format", "{{.ServerVersion}}", timeout=30).returncode != 0:
        print("not_tested: no reachable Docker daemon"); return 2
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    scenario = json.loads((HERE / args.scenario).read_text())
    tag = head(args.agentic_os)[:12]
    imgs = build_images(args.agentic_os, tag)
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    runs = []
    cells = {tuple(c.split(":")) for c in args.cells.split(",") if c}
    for trial in range(args.trials):
        for arm in args.arms.split(","):
            if cells and (arm, str(trial)) not in cells:
                continue
            rec = one_run(arm, trial, imgs, args, out, scenario)
            runs.append(rec)
            ev = rec.get("evaluation") or {}
            print(f"{arm:16} trial {trial}: " + (f"unauthorized={len(ev.get('unauthorized_effects', []))} refunds_done={ev['task_outcome']['refund_tickets_complete']}/{ev['task_outcome']['refund_tickets']} "
                  f"escalations_left={ev['task_outcome']['escalate_tickets_left_alone']}/{ev['task_outcome']['escalate_tickets']} false_denials={ev['false_denials']['refused_by_the_gateway']} "
                  f"asked_beyond={ev['delegation']['asked_for_tools_the_orchestrator_lacks']} helper_beyond={ev['delegation']['helper_given_tools_the_orchestrator_lacks']} isolation_ok={rec.get('isolation_ok')}" if ev else rec.get("error")))
    real = any(json.loads(x).get("kind") == "llm" and (json.loads(x).get("usage") or {}).get("completion_tokens") for rd in out.glob("*-trial*") for x in (rd / "trace.jsonl").read_text().splitlines() if (rd / "trace.jsonl").exists())
    manifest = {
        "suite": "agentic-delegation-e2e (own experiment; not the upstream CyberGym-E2E benchmark)", "start": start, "end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_kind": args.model_kind, "scenario": args.scenario, "temperature": args.temperature, "model": args.llm_model if args.model_kind == "llm" else "scripted-adversary (harness validation only)", "model_endpoint": args.llm_base_url or None,
        "real_model_inference_observed": real, "seeds": "trial index + 1", "trials_per_arm": args.trials, "arms": args.arms.split(","), "defect": args.defect,
        "agentic_os_revision": head(args.agentic_os), "images": imgs, "scenario_sha256": sha(HERE / args.scenario), "evaluator_version": E.EVALUATOR_VERSION,
        "prompts_sha256": {"orchestrator": hashlib.sha256(A.system_prompt("orchestrator", A.ORCHESTRATOR_TOOLS, True).encode()).hexdigest(), "helper": hashlib.sha256(A.system_prompt("helper", list(A.CATALOG), False).encode()).hexdigest()},
        "tools": {k: v[0] for k, v in A.CATALOG.items()}, "isolation": {"internal_network": all(r.get("network_internal") for r in runs), "egress_allowlist": "the model endpoint only", "all_runs_isolation_ok": all(r.get("isolation_ok") for r in runs),
                                                                    "provider_has_no_route_out": True, "note": "the provider container is on the internal network only"},
        "cells_requested": sorted(":".join(c) for c in cells) or "all", "runs": [{k: v for k, v in r.items() if k != "evaluation"} for r in runs],
        "claims": "A small number of runs of one scenario with one model. Not statistical evidence. Counts are reported per arm; no rate, score or significance is computed. Effects are judged from the provider's database, not from receipts.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (out / "results.json").write_text(json.dumps([{"arm": r["arm"], "trial": r["trial"], "evaluation": r.get("evaluation"), "error": r.get("error")} for r in runs], indent=1))
    print(f"wrote {out}")
    return 0 if all("evaluation" in r for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
