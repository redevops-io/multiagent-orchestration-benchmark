#!/usr/bin/env python3
"""Write the run manifest for one arm from what the run produced (the workflow's last step before `report.py`). It computes digests of the files it is given and derives `status` from the results:
`completed` only if every planned task has a row in both modes, otherwise `partial`. It records what the workflow says about isolation; it cannot verify it (that is the leakage audit's job)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cybergym_e2e import manifest as M          # noqa: E402

HERE = Path(__file__).resolve().parent


def build_manifest(*, arm: str, run_id: str, models: list, provider: str, timeout_s: int, cost_cap_usd: float, seed: int, runner_image_digest: str, start: str, end: str,
                   result_uri: str, results_path: Path, image_digests_path: "Path | None", planned: list, pins: dict, firewall: bool, allowlist: list, provider_tools_disabled: bool,
                   environment: str, extra_files: "dict | None" = None) -> dict:
    results = json.loads(Path(results_path).read_text())
    done = {(r["task"], r["mode"]) for r in results}
    complete = all((t, m) in done for t in planned for m in M_MODES)
    digests = {"results.json": M.sha256_file(results_path)}
    for name, p in (extra_files or {}).items():
        if p and Path(p).exists():
            digests[name] = M.sha256_file(p)
    m = {"suite": "cybergym-e2e", "run_id": run_id, "arm": arm,
         "source_revisions": {"upstream": pins["upstream"]["revision"], "dataset": pins["dataset"]["revision"]},
         "model_ids": models, "provider": provider, "budget": {"agent_timeout_sec": timeout_s, "cost_cap_usd": cost_cap_usd, "max_attempts": 1}, "seed": seed,
         "runner_image_digest": runner_image_digest, "start": start, "end": end, "result_uri": result_uri, "evidence_digests": digests,
         "evaluator_version": M.EVALUATOR_VERSION, "environment": environment, "status": "completed" if complete else "partial", "planned_tasks": planned,
         "isolation": {"firewall": firewall, "allowlist": allowlist, "provider_side_tools_disabled": provider_tools_disabled}}
    if image_digests_path and Path(image_digests_path).exists():
        m["image_digests"] = json.loads(Path(image_digests_path).read_text())
    return m


M_MODES = ("e2e", "patch-only")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--provider", required=True)
    ap.add_argument("--timeout-s", type=int, required=True)
    ap.add_argument("--cost-cap-usd", type=float, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--runner-image-digest", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--result-uri", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--image-digests")
    ap.add_argument("--planned", default=str(HERE / "pilot" / "pilot-1.json"))
    ap.add_argument("--allowlist", nargs="+", required=True)
    ap.add_argument("--firewall", choices=["true", "false"], required=True)
    ap.add_argument("--provider-tools-disabled", choices=["true", "false"], required=True)
    ap.add_argument("--environment", default="isolated-runner")
    ap.add_argument("--extra", nargs="*", default=[], help="name=path of further evidence files to digest")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pins = json.loads((HERE / "PINS.json").read_text())
    m = build_manifest(arm=a.arm, run_id=a.run_id, models=a.models, provider=a.provider, timeout_s=a.timeout_s, cost_cap_usd=a.cost_cap_usd, seed=a.seed,
                       runner_image_digest=a.runner_image_digest, start=a.start, end=a.end, result_uri=a.result_uri, results_path=Path(a.results),
                       image_digests_path=Path(a.image_digests) if a.image_digests else None, planned=json.loads(Path(a.planned).read_text())["tasks"], pins=pins,
                       firewall=a.firewall == "true", allowlist=a.allowlist, provider_tools_disabled=a.provider_tools_disabled == "true", environment=a.environment,
                       extra_files=dict(x.split("=", 1) for x in a.extra))
    Path(a.out).write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    print(f"wrote {a.out}: status {m['status']}, problems {M.problems(m) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
