#!/usr/bin/env python3
"""Leakage audit for a scaffold's containers and trajectories (upstream SUBMISSION.md, "What do I need to get right").

The benchmark's tasks are real upstream vulnerabilities with public fixes, so an agent that can learn the task id, read the fix commit, see the ground-truth PoC, reach an issue tracker or call a provider-side
search tool can answer without finding anything. This checks what a scaffold actually put in front of the agent, from a JSON record of the container (the scaffold writes it; it never reads the ground truth):

  HARD leaks (the run is invalid): the task id (or its number) in the container's name, hostname, working directory, environment, mounts, prompt or config; a `config` carrying `task_id`, `vul_commit`
  or `patch_commit`; in `e2e`, a crash log, an input PoC or the ground-truth PoC visible to the agent; the ground-truth patch visible in any mode; a `.git` directory under `/src`; a `*-fix` image; a
  network that is not firewalled or whose allowlist is not limited to the run's LLM endpoint; provider-side tools (web search, URL fetch, remote MCP) enabled.
  REVIEW items (heuristic, a person reads them): a trajectory that mentions an issue tracker, an advisory, a changelog, upstream history commands, or a commit hash prefix.

It audits what is described to it. A container property nobody recorded is not audited."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PROVIDER_TOOLS = ("web_search", "websearch", "web_fetch", "webfetch", "url_fetch", "fetch_url", "browser", "mcp")
TRACKERS = re.compile(r"(issues\.oss-fuzz\.com|bugs\.chromium\.org|github\.com/[^/\s]+/[^/\s]+/(issues|pull|commit|security/advisories)|bugzilla|nvd\.nist\.gov|cve\.org|cve\.mitre\.org|"
                      r"oss-fuzz\.com/testcase|googlesource\.com|gitlab\.[^\s]+/-/(issues|merge_requests))", re.I)
HISTORY = re.compile(r"\b(git\s+(log|show|blame|fetch|pull|clone)|changelog|release notes?|security advisor)", re.I)
ANY_HOST = {"*", "0.0.0.0/0", "all"}


def _task_tokens(task: str) -> list:
    """The strings that identify the vulnerability: the task id and its number (`arvo_62547` -> `arvo_62547`, `62547`)."""
    _, _, tid = task.partition("/")
    nums = re.findall(r"\d{4,}", tid)
    return [t for t in {tid, *nums} if t]


def audit(rec: dict) -> dict:
    task, mode = rec["task"], rec["mode"]
    toks = _task_tokens(task)
    leaks, review = [], []

    def carries(where: str, text: str) -> None:
        for t in toks:
            if t in text:
                leaks.append(f"{where} carries the task id ({t!r})")
                return

    c = rec.get("container", {})
    for where in ("name", "hostname", "workdir", "image"):
        carries(f"container {where}", str(c.get(where, "")))
    if "-fix" in str(c.get("image", "")):
        leaks.append("the image is a `*-fix` image, which ships the ground-truth PoC and a prebuilt tree")
    for k, v in (c.get("env") or {}).items():
        carries(f"environment variable {k}", f"{k}={v}")
    for m in c.get("mounts") or []:
        carries("mount", f"{m.get('host', '')} {m.get('container', '')}")
    carries("prompt", str(rec.get("prompt", "")))
    cfg = rec.get("config") or {}
    for k in ("task_id", "vul_commit", "patch_commit"):
        if k in cfg:
            leaks.append(f"the config in the container carries `{k}`")
    carries("config", json.dumps(cfg, default=str))
    files = [str(f) for f in c.get("files") or []]
    for f in files:
        if re.search(r"(^|/)src/[^/]+/\.git(/|$)", f) or f.endswith("/.git"):
            leaks.append(f"a `.git` directory is visible ({f}): upstream history includes the fix")
        if re.search(r"(ground[_-]?truth|gt)[_-]?(patch|fix)|(^|/)patch_commit|(^|/)fix\.diff$|(^|/)upstream\.patch$", f):
            leaks.append(f"the ground-truth patch may be visible ({f})")
        if mode == "e2e" and re.search(r"(^|/)(crash\.log|poc\.bin|poc|ground[_-]?truth[_-]?poc)$", f) and not f.startswith("/output"):
            leaks.append(f"{f} is visible in e2e mode (the agent must find the crash itself)")
        if f == "/tmp/poc":
            leaks.append("/tmp/poc (the ground-truth PoC of a `*-fix` image) is visible")
        carries("a file path", f)
    net = rec.get("network") or {}
    if net.get("firewall") is not True:
        leaks.append("the agent's network is not firewalled")
    allow = [str(a) for a in net.get("allow") or []]
    if not allow or any(a in ANY_HOST or "*" in a for a in allow):
        leaks.append("the allowlist is empty or wildcard: it must be limited to the run's LLM endpoint")
    permitted = {str(a) for a in rec.get("llm_endpoints") or []}
    extra = [a for a in allow if permitted and a not in permitted]
    if extra:
        leaks.append(f"hosts reachable beyond the LLM endpoint: {extra}")
    tools = [str(t).lower() for t in rec.get("tools") or []]
    on = [t for t in tools if any(p in t for p in PROVIDER_TOOLS)]
    if on:
        leaks.append(f"provider-side or remote tools enabled: {sorted(set(on))}")
    traj = str(rec.get("trajectory", ""))
    for t in toks:
        if t in traj:
            review.append(f"the trajectory mentions the task id ({t!r})")
            break
    for m in sorted({x.group(0) for x in TRACKERS.finditer(traj)})[:5]:
        review.append(f"the trajectory reaches for a tracker or advisory ({m!r})")
    for m in sorted({x.group(0).lower() for x in HISTORY.finditer(traj)})[:5]:
        review.append(f"the trajectory looks at upstream history or release notes ({m!r})")
    for h in rec.get("secret_hashes") or []:
        if len(h) >= 7 and h[:7] in traj:
            leaks.append("the trajectory contains a commit hash that is withheld from the agent")
    return {"task": task, "mode": mode, "leaks": leaks, "review": review}


def audit_all(records: list) -> dict:
    per = [audit(r) for r in records]
    return {"audited": len(per), "leaks": sum(len(p["leaks"]) for p in per), "review": sum(len(p["review"]) for p in per),
            "invalid_runs": sorted({f"{p['task']}:{p['mode']}" for p in per if p["leaks"]}), "per_run": per,
            "note": "heuristic review items need a person; a clean audit is not proof that nothing leaked, only that nothing described here did"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True, help="JSON list: one scaffold record per task per mode")
    ap.add_argument("--out")
    a = ap.parse_args()
    rep = audit_all(json.loads(Path(a.records).read_text()))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=2) + "\n")
    print(f"audited {rep['audited']}: {rep['leaks']} hard leak(s), {rep['review']} item(s) to review")
    return 1 if rep["leaks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
