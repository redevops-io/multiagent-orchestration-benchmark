#!/usr/bin/env python3
"""Assemble the submission-format `results.json` from the per-task run directories the upstream runner writes (`agent_output/<project>_<task>/<timestamp>_<mode>/summary.json`).

Upstream's runner and validator write one `summary.json` per task and mode (with an `attempts` list) but no combined `results.json`; its submission guide asks for one object per task per mode. This builds it
from what the runner recorded, adding nothing: stage values are copied as recorded (`None` in patch-only becomes `skipped`, as the guide says).

It also writes the INFRASTRUCTURE sidecar (`--infra-out`): a run directory with no `summary.json`, a `summary.json` with no attempt, or a run whose status is `error` with nothing attempted means the harness
broke before the agent could be judged. Those tasks get an all-`error` row in `results.json` (so every attempted task is still reported) and an entry in the sidecar saying why. A validator `error` inside a
recorded attempt is NOT infrastructure (a PoC that hangs produces one) and stays an ordinary row.

Read-only on the run tree. The directory-name layout is read from upstream's `run_agent.py` at the pinned revision; a layout it does not recognise is reported, not guessed."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STAGES = ("stage1", "stage2", "stage3", "stage4")
MODE_OF = {"e2e": "e2e", "patch": "patch-only"}


def _row(task: str, mode: str, att: "dict | None") -> dict:
    if att is None:
        return {"task": task, "mode": mode, **{s: "error" for s in STAGES}, "agent_exec_seconds": 0.0}
    row = {"task": task, "mode": mode}
    for s in STAGES:
        v = att.get(s)
        row[s] = "skipped" if v is None and mode == "patch-only" and s in ("stage1", "stage2") else (v if v is not None else "skipped")
    row["agent_exec_seconds"] = float(att.get("agent_exec_seconds", 0.0))
    return row


def assemble(root: Path, planned: list) -> tuple:
    """(rows, infra sidecar, problems). `planned` are `<project>/<task_id>`; run directories are matched to them by name."""
    rows, infra, problems = [], {}, []
    for task in planned:
        proj, _, tid = task.partition("/")
        task_dir = root / f"{proj}_{tid}"
        runs = sorted(p for p in task_dir.iterdir() if p.is_dir()) if task_dir.is_dir() else []
        by_mode: dict = {}
        for d in runs:
            m = re.match(r"^\d{8}_\d{6}_(e2e|patch)(_x\d+)?$", d.name)
            if not m:
                problems.append(f"{task}: unrecognised run directory {d.name!r}")
                continue
            by_mode.setdefault(MODE_OF[m.group(1)], []).append(d)
        for mode in ("e2e", "patch-only"):
            ds = by_mode.get(mode, [])
            if len(ds) > 1:
                problems.append(f"{task}:{mode}: {len(ds)} run directories (one attempt per task is accepted); the latest is used and the rest are listed here")
            if not ds:
                continue                                              # not run: the report lists it as not_run
            d = ds[-1]
            try:
                summ = json.loads((d / "summary.json").read_text())
            except (OSError, ValueError):
                rows.append(_row(task, mode, None))
                infra[f"{task}:{mode}"] = "no readable summary.json in the run directory: the harness broke before the agent could be judged"
                continue
            atts = summ.get("attempts") or []
            if not atts:
                rows.append(_row(task, mode, None))
                infra[f"{task}:{mode}"] = f"no attempt was recorded (runner status {summ.get('status')!r})"
                continue
            if len(atts) > 1:
                problems.append(f"{task}:{mode}: {len(atts)} attempts recorded (one attempt per task is accepted); the last is used")
            rows.append(_row(task, mode, atts[-1]))
    return rows, infra, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-output", required=True, help="the runner's output directory (upstream default: agent_output)")
    ap.add_argument("--planned", required=True, help="a pilot file (or any JSON with a `tasks` list)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--infra-out", required=True)
    a = ap.parse_args()
    planned = json.loads(Path(a.planned).read_text())["tasks"]
    rows, infra, problems = assemble(Path(a.agent_output), planned)
    Path(a.out).write_text(json.dumps(rows, indent=2) + "\n")
    Path(a.infra_out).write_text(json.dumps(infra, indent=2, sort_keys=True) + "\n")
    print(f"{len(rows)} rows, {len(infra)} infrastructure failure(s), {len(problems)} problem(s)")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
