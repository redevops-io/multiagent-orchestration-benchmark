"""Pilot selection: a small, stratified, seeded sample of the pinned upstream task list.

Strata are the project (at most one task per project, so one project's quirks cannot dominate) and the source family (`arvo` or `oss-fuzz`, kept in roughly their share of the list). The seed, the
upstream revision and the hash of the task list are written with the sample, so the choice is reproducible and cannot be quietly re-drawn after seeing results. A pilot is a feasibility and
infrastructure check, not an estimate of anything: it is far too small for a rate with a useful interval, and the larger sample is pre-registered separately (see PREREGISTRATION_TEMPLATE.md)."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def parse(tasks_text: str) -> list:
    out = []
    for line in tasks_text.split():
        proj, _, tid = line.partition("/")
        if proj and tid:
            out.append((proj, tid, "oss-fuzz" if tid.startswith("oss-fuzz") else "arvo" if tid.startswith("arvo") else "other"))
    return out


def select(tasks: list, n: int, seed: int) -> list:
    rng = random.Random(seed)
    by_proj: dict = {}
    for p, t, fam in tasks:
        by_proj.setdefault(p, []).append((p, t, fam))
    share: dict = {}
    for _, _, fam in tasks:
        share[fam] = share.get(fam, 0) + 1
    total = sum(share.values())
    quota = {fam: max(1, round(n * c / total)) for fam, c in share.items()}
    while sum(quota.values()) > n:                                   # trim the largest family first
        quota[max(quota, key=quota.get)] -= 1
    while sum(quota.values()) < n:
        quota[max(share, key=share.get)] += 1
    chosen: list = []
    used: set = set()
    for fam in sorted(quota, key=lambda f: -quota[f]):
        pool = [(p, rng.choice([x for x in v if x[2] == fam])) for p, v in sorted(by_proj.items()) if any(x[2] == fam for x in v)]
        rng.shuffle(pool)
        for p, task in pool:
            if len([c for c in chosen if c[2] == fam]) >= quota[fam]:
                break
            if p in used:
                continue
            chosen.append(task)
            used.add(p)
    return sorted(chosen)


def build(tasks_path: Path, n: int, seed: int, pins: dict) -> dict:
    text = tasks_path.read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()
    if sha != pins["upstream"]["tasks_sha256"]:
        raise SystemExit("the task list is not the one pinned in PINS.json")
    picked = select(parse(text), n, seed)
    return {"name": "pilot", "seed": seed, "n": len(picked), "upstream_revision": pins["upstream"]["revision"], "tasks_sha256": sha,
            "strata": "one task per project; source family in proportion to its share of the list",
            "tasks": [f"{p}/{t}" for p, t, _ in picked],
            "note": "A pilot checks that the harness runs end to end and that infrastructure failures are separable from agent failures. It is not an estimate of any rate."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--out", default=str(HERE / "pilot" / "pilot-1.json"))
    a = ap.parse_args()
    pins = json.loads((HERE / "PINS.json").read_text())
    doc = build(HERE / "pilot" / "upstream_tasks.txt", a.n, a.seed, pins)
    Path(a.out).write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {a.out}: {doc['n']} tasks, {len({t.split('/')[0] for t in doc['tasks']})} projects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
