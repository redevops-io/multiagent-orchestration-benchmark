#!/usr/bin/env python3
"""Build the leakage audit's record of one agent container from the container itself, while it exists.

    python3 cybergym_e2e/collect_audit.py --container NAME --task proj/arvo_123 --mode e2e --firewall-network NET --llm-endpoint HOST [--config /config/config.toml] [--prompt-file F] [--trajectory F] --out rec.json

`leakage.audit` can only judge what it is told. This reads it from Docker: `docker inspect` (name, hostname, working directory, image, environment, mounts, network mode), `docker exec find` (the files the agent can see in
the directories an agent works in, plus any file anywhere with a name that points at the answer), and the config file the scaffold put in the container. What it cannot know it says so: the firewall is `true` only if the container is
on the named firewall network AND has proxy variables pointing at it; the enabled tool list is recorded only if the caller gives one, otherwise only the explicit disallow list is. It never reads the ground truth.

Limits: it captures a moment (call it right after the scaffold finishes setting the container up, before the agent runs; the agent can create files later, which the trajectory review is for). Upstream's runner creates and removes its
containers itself and names them with a random id, so tying a container to a task and mode is the caller's job: this does not hook into the runner. Written against Docker's documented `inspect` JSON and tested on synthetic output and one real
throwaway container; not run against upstream's own containers."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Callable, Optional

SCAN_DIRS = ("/src", "/data", "/output", "/work", "/config", "/tmp", "/home")
SENSITIVE_NAMES = ("crash.log", "poc.bin", "poc", "fix.patch", "upstream.patch", "fix.diff", ".git")
MAX_FILES = 20000


class Docker:
    """The three docker operations this needs, over the CLI. Replace in tests."""

    def __init__(self, run: Optional[Callable] = None) -> None:
        self._run = run or (lambda args: subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60))

    def inspect(self, container: str) -> dict:
        r = self._run(["inspect", container])
        if r.returncode != 0:
            raise RuntimeError(f"docker inspect failed: {r.stderr.strip()[:200]}")
        return json.loads(r.stdout)[0]

    def exec(self, container: str, cmd: str) -> str:
        r = self._run(["exec", container, "sh", "-c", cmd])
        return r.stdout if r.returncode in (0, 1) else ""         # find exits 1 when a directory is missing; the lines it did print are still wanted

    def read(self, container: str, path: str) -> Optional[str]:
        r = self._run(["exec", container, "cat", path])
        return r.stdout if r.returncode == 0 else None


def _env(lst: list) -> dict:
    out = {}
    for item in lst or []:
        k, _, v = str(item).partition("=")
        out[k] = v
    return out


def _files(docker: Docker, container: str, scan_dirs: tuple) -> tuple:
    lines: list = []
    for d in scan_dirs:
        lines += docker.exec(container, f"find {d} -maxdepth 4 2>/dev/null").splitlines()
    names = " -o ".join(f"-name '{n}'" for n in SENSITIVE_NAMES)
    lines += docker.exec(container, f"find / -xdev \\( -path /proc -o -path /sys -o -path /dev \\) -prune -o \\( {names} \\) -print 2>/dev/null").splitlines()
    seen, out = set(), []
    for ln in lines:
        ln = ln.strip()
        if ln and ln not in seen:
            seen.add(ln)
            out.append(ln)
    truncated = len(out) > MAX_FILES
    return out[:MAX_FILES], truncated


def collect(docker: Docker, container: str, *, task: str, mode: str, firewall_network: Optional[str], llm_endpoints: list, allow: Optional[list] = None, config_path: Optional[str] = None,
            prompt: str = "", trajectory: str = "", tools: Optional[list] = None, tools_disallowed: Optional[list] = None, mcp_servers: Optional[list] = None,
            secret_hashes: Optional[list] = None, scan_dirs: tuple = SCAN_DIRS) -> dict:
    info = docker.inspect(container)
    cfg = info.get("Config") or {}
    env = _env(cfg.get("Env"))
    net_mode = ((info.get("HostConfig") or {}).get("NetworkMode")) or ""
    proxies = [env.get(k, "") for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")]
    firewalled = bool(firewall_network) and net_mode == firewall_network and any(proxies)
    files, truncated = _files(docker, container, scan_dirs)
    config: dict = {}
    if config_path:
        text = docker.read(container, config_path)
        if text is not None:
            try:
                config = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                config = {"_unparsed": text[:500]}
    rec = {"task": task, "mode": mode, "prompt": prompt, "trajectory": trajectory, "llm_endpoints": list(llm_endpoints),
           "container": {"name": str(info.get("Name", "")).lstrip("/"), "hostname": cfg.get("Hostname", ""), "workdir": cfg.get("WorkingDir", ""), "image": cfg.get("Image", ""), "env": env,
                         "mounts": [{"host": m.get("Source", ""), "container": m.get("Destination", ""), "rw": m.get("RW")} for m in info.get("Mounts") or []], "files": files},
           "files_truncated": truncated, "config": config,
           "network": {"firewall": firewalled, "mode": net_mode, "proxies": [p for p in proxies if p], "allow": list(allow if allow is not None else llm_endpoints)},
           "tools": tools, "tools_disallowed": tools_disallowed, "mcp_servers": list(mcp_servers or []), "secret_hashes": list(secret_hashes or [])}
    if tools is None and tools_disallowed is None:
        rec["tools_disallowed"] = None                              # nothing recorded: leakage reports the provider-side tools as not shown to be disabled
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--mode", required=True, choices=["e2e", "patch-only"])
    ap.add_argument("--firewall-network")
    ap.add_argument("--llm-endpoint", action="append", default=[])
    ap.add_argument("--allow", action="append")
    ap.add_argument("--config")
    ap.add_argument("--prompt-file")
    ap.add_argument("--trajectory")
    ap.add_argument("--tools-disallowed", nargs="*")
    ap.add_argument("--mcp-server", action="append", default=[])
    ap.add_argument("--secret-hash", action="append", default=[], help="a commit hash withheld from the agent (the collector never reads the ground truth: pass these in)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rec = collect(Docker(), a.container, task=a.task, mode=a.mode, firewall_network=a.firewall_network, llm_endpoints=a.llm_endpoint, allow=a.allow, config_path=a.config,
                  prompt=Path(a.prompt_file).read_text() if a.prompt_file else "", trajectory=Path(a.trajectory).read_text() if a.trajectory else "",
                  tools_disallowed=a.tools_disallowed, mcp_servers=a.mcp_server, secret_hashes=a.secret_hash)
    Path(a.out).write_text(json.dumps(rec, indent=2) + "\n")
    print(f"wrote {a.out}: {len(rec['container']['files'])} files seen, firewall={rec['network']['firewall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
