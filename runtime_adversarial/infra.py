#!/usr/bin/env python3
"""Infrastructure checks of the runtime-adversarial lane: things that need a container daemon, a repository scan or a log file rather than a simulated mission.

    python3 runtime_adversarial/infra.py --agentic-os PATH [--only network_isolation,image_pinning,log_chain] [--write-baseline] [--out FILE]

Each check returns pass, fail or not_tested. `not_tested` is reserved for infrastructure that is genuinely absent (no Docker daemon, no way to pull the base image); a check that RAN and found a property broken is `fail`, and a check that did not
run is never reported as a pass. Every check carries a positive control (the thing it looks for is shown to be detectable) and has a defect-seeded variant (`RA_NETWORK_BUG`, `RA_PINNING_BUG`, `RA_LOG_BUG`) that CI runs and expects to fail.

    network_isolation  (I02) a throwaway INTERNAL docker network, the repository's own egress proxy image, an origin that logs every request it receives. A process that ignores the proxy reaches nothing; through the proxy only what the allowlist
                       names is reached; the ORIGIN'S log, not the proxy's, is the oracle for what got through. The Kubernetes policy is checked statically; its enforcement needs a policy-enforcing cluster and is reported as not tested.
    image_pinning      (I04) every image reference in the repository's Dockerfiles, compose files and Kubernetes manifests is classified immutable (a full sha256 digest) or mutable. The classifier is checked against seeded fixtures; the
                       repository's own mutable references are listed as open findings and may only shrink (`image_pin_baseline.json`). Whether a pinned digest exists in a registry needs the network and is reported as not tested.
    log_chain          (I05) the mission event log's hash chain against in-place edits, deletions, reordering, insertions, replays and hash-stripping, and against what the chain alone cannot see (tail truncation, a consistent rewrite) with and without an
                       anchored head."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PASS, FAIL, NOT_TESTED = "pass", "fail", "not_tested"


class R:
    def __init__(self, cid: str):
        self.id, self.checks, self.findings, self.limits, self.status, self.reason, self.meta = cid, [], [], [], PASS, "", {}

    def check(self, ok: bool, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail if not ok else ""})
        if not ok:
            self.status = FAIL

    def not_tested(self, reason: str) -> "R":
        self.status, self.reason = NOT_TESTED, reason
        return self

    def as_dict(self) -> dict:
        bad = [c for c in self.checks if not c["ok"]]
        return {"id": self.id, "status": self.status, "reason": self.reason, "checks": len(self.checks), "failed": bad[:12], "open_findings": self.findings, "known_limits": self.limits, "meta": self.meta}


# ─────────────────────────────── image pinning ───────────────────────────────

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def classify_ref(ref: str) -> str:
    ref = ref.strip().strip("'\"")
    name = ref.split("@", 1)[0]
    if "$" in name:
        return "mutable"                                          # a registry, name or tag chosen at deploy time is not a pin
    return "immutable" if DIGEST.search(ref) else "mutable"


def _dockerfile_refs(text: str) -> list:
    stages, refs = set(), []
    for line in text.splitlines():
        m = re.match(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
        if not m:
            continue
        image, stage = m.group(1), m.group(2)
        if image.lower() != "scratch" and image not in stages:
            refs.append(image)
        if stage:
            stages.add(stage)
    return refs


def _yaml_refs(text: str) -> list:
    refs = []
    for line in text.splitlines():
        m = re.match(r"^\s*-?\s*image:\s*(\S+)", line)
        if m and not m.group(1).startswith("#"):
            refs.append(m.group(1).strip("'\""))
    return refs


def scan_images(root: Path) -> list:
    """[(file, ref, class)] over the tracked files of a checkout."""
    try:
        files = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    except Exception:  # noqa: BLE001
        files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    out = []
    for f in files:
        base = os.path.basename(f)
        is_df = base.startswith("Dockerfile") or base.endswith(".Dockerfile")
        is_yaml = f.endswith((".yml", ".yaml")) and not f.startswith((".github/",)) and "node_modules" not in f
        if not (is_df or is_yaml):
            continue
        try:
            text = (root / f).read_text(errors="replace")
        except OSError:
            continue
        for ref in (_dockerfile_refs(text) if is_df else _yaml_refs(text)):
            out.append((f, ref, classify_ref(ref)))
    return out


# the classifier's truth table: reference text -> what an operator must be told
FIXTURES = [
    ("python:3.12-slim", "mutable"), ("python:latest", "mutable"), ("redis", "mutable"), ("ghcr.io/o/app:stable", "mutable"), ("${REGISTRY:-ghcr.io/o}/app:${TAG:-stable}", "mutable"),
    ("python@sha256:" + "a" * 64, "immutable"), ("python:3.12-slim@sha256:" + "0123456789abcdef" * 4, "immutable"), ("ghcr.io/o/app@sha256:" + "f" * 64, "immutable"),
    ("python@sha256:" + "a" * 63, "mutable"), ("python@sha256:" + "a" * 65, "mutable"), ("python@sha256:" + "A" * 64, "mutable"), ("python@sha256:" + "g" * 64, "mutable"),
    ("python@sha1:" + "a" * 40, "mutable"), ("python@sha256:", "mutable"), ("${BASE}@sha256:" + "a" * 64, "mutable"), ("python:3.12@sha256:" + "a" * 64 + "-x", "mutable"),
]
DOCKERFILE_FIXTURE = ("ARG BASE=python:3.12\nFROM scratch\nFROM --platform=linux/amd64 python:3.12 AS build\nFROM build AS test\nFROM python@sha256:" + "b" * 64 + "\nFROM ${BASE}\n")
DOCKERFILE_EXPECT = ["python:3.12", "python@sha256:" + "b" * 64, "${BASE}"]          # scratch and stage names are not images
YAML_FIXTURE = "services:\n  a:\n    image: redis:7\n  b:\n    image: 'pg@sha256:" + "c" * 64 + "'\n  c:\n    image: \"${R}/x:${T}\"  # why\n    # image: commented\n  d:\n    - image: y:1\n"
YAML_EXPECT = ["redis:7", "pg@sha256:" + "c" * 64, "${R}/x:${T}", "y:1"]


def check_image_pinning(agentic_os: str, write_baseline: bool = False) -> R:
    r = R("image_pinning")
    bug = os.environ.get("RA_PINNING_BUG", "")
    cls = (lambda ref: "immutable") if bug == "accept_all" else classify_ref        # the seeded defect: a checker that accepts every reference
    for ref, want in FIXTURES:
        r.check(cls(ref) == want, f"classify {ref[:48]!r} as {want}", f"classified as {cls(ref)}")
    r.check(_dockerfile_refs(DOCKERFILE_FIXTURE) == DOCKERFILE_EXPECT, "Dockerfile parsing: scratch and stage names are skipped, variables are kept", str(_dockerfile_refs(DOCKERFILE_FIXTURE)))
    r.check(_yaml_refs(YAML_FIXTURE) == YAML_EXPECT, "YAML parsing: quoted, list-item and trailing-comment forms; commented-out lines ignored", str(_yaml_refs(YAML_FIXTURE)))
    root = Path(agentic_os)
    found = [(f, ref, cls(ref)) for f, ref, _ in scan_images(root)]
    r.check(len(found) > 0, "the repository's image references were found (a scan that finds nothing proves nothing)", "no references found")
    mutable = sorted({(f, ref) for f, ref, c in found if c == "mutable"})
    immutable = [x for x in found if x[2] == "immutable"]
    base_path = HERE / "image_pin_baseline.json"
    if write_baseline:
        base_path.write_text(json.dumps({"note": "Mutable image references that are open findings in the pinned agentic-os. This list may only shrink; a new entry fails the lane. Regenerate deliberately with --write-baseline when bumping PINS.json.",
                                         "revision": _head(root), "open": [{"file": f, "ref": ref} for f, ref in mutable]}, indent=1) + "\n")
    base = json.loads(base_path.read_text()) if base_path.exists() else {"open": [], "revision": None}
    known = {(e["file"], e["ref"]) for e in base["open"]}
    new = [m for m in mutable if m not in known]
    r.check(not new, f"no mutable image reference outside the recorded baseline ({len(new)} new)", "; ".join(f"{f}: {ref}" for f, ref in new[:5]))
    r.findings = [f"{f}: {ref}" for f, ref in mutable]
    r.meta = {"references": len(found), "immutable": len(immutable), "mutable_open": len(mutable), "baseline_revision": base.get("revision"), "baseline_entries": len(known),
              "stale_baseline_entries": len(known - set(mutable)), "digest_exists_in_registry": "not_tested: needs network access to each registry; run locally with a registry client"}
    return r


def _head(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


# ─────────────────────────────── log chain ───────────────────────────────

def check_log_chain(agentic_os: str) -> R:
    r = R("log_chain")
    bug = os.environ.get("RA_LOG_BUG", "")
    sys.path.insert(0, agentic_os)
    from agentic_os.mission import store as S            # noqa: PLC0415
    verify = (lambda events: S.ChainReport(True, len(events), 0)) if bug == "no_verify" else S.verify_chain
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "events.jsonl"
        es = S.EventStore(str(src))
        seq = [("a", "mission.created"), ("b", "mission.created"), ("a", "step.started"), ("b", "step.started"), ("a", "effect.recorded"), ("a", "step.finished"), ("b", "effect.recorded"), ("b", "mission.done"), ("a", "mission.done")]
        for i, (m, t) in enumerate(seq):
            es.append(t, m, {"n": i, "amount": 10 * i, "who": "agent"})
        heads = {m: es.head(m) for m in ("a", "b")}
        base = verify(S.EventStore(str(src)).all())
        r.check(base.ok and base.unchained == 0 and base.checked == len(seq), "an untouched log verifies, every event covered", str(base))
        lines = src.read_text().splitlines()

        def load(mut) -> "tuple[object, dict]":
            p = td / f"t{uuid.uuid4().hex[:6]}.jsonl"
            new = mut([json.loads(x) for x in lines])
            p.write_text("".join(json.dumps(x) + "\n" for x in new))
            st = S.EventStore(str(p))
            return verify(st.all()), {m: st.head(m) for m in ("a", "b")}

        def rehash(rows: list, start: int) -> list:
            """what an attacker with write access and the source code can do: recompute the chain from `start` on"""
            prev = {}
            for i, x in enumerate(rows):
                if i >= start:
                    x["prev"] = prev.get(x["mission_id"], "")
                    x["hash"] = S.entry_hash(x["prev"], x["seq"], x["ts"], x["type"], x["mission_id"], x["payload"])
                prev[x["mission_id"]] = x["hash"]
            return rows

        def edit(rows):
            rows[4]["payload"]["amount"] = 1
            return rows

        def delete(rows):
            del rows[3]
            return rows

        def reorder(rows):
            rows[2], rows[4] = rows[4], rows[2]
            return rows

        def insert(rows):
            f = dict(rows[2]); f["payload"] = {"n": 99, "amount": 9999, "who": "attacker"}
            rows.insert(3, f)
            return rows

        def replay(rows):
            rows.append(dict(rows[4]))
            return rows

        def strip_last_of_mission(rows):                      # the hash of a mission's final event removed after editing it: "legacy, unchained"
            x = rows[-1]; x["payload"]["amount"] = 7; x.pop("hash"); x.pop("prev")
            return rows

        def strip_whole_mission(rows):
            for x in rows:
                if x["mission_id"] == "b":
                    x["payload"]["amount"] = 0; x.pop("hash"); x.pop("prev")
            return rows

        def truncate_tail(rows):
            return rows[:-2]

        def forged_extension(rows):
            last = [x for x in rows if x["mission_id"] == "a"][-1]
            f = {"seq": rows[-1]["seq"] + 1, "ts": rows[-1]["ts"] + 1, "type": "effect.recorded", "mission_id": "a", "payload": {"amount": 5000, "who": "attacker"}, "prev": last["hash"]}
            f["hash"] = S.entry_hash(f["prev"], f["seq"], f["ts"], f["type"], f["mission_id"], f["payload"])
            rows.append(f)
            return rows

        def consistent_rewrite(rows):
            rows[4]["payload"]["amount"] = 1
            return rehash(rows, 4)

        # detected by the chain alone: the chain's job
        for name, mut in (("an event edited in place", edit), ("an event deleted from the middle", delete), ("two events reordered", reorder), ("a forged event inserted", insert), ("an event replayed", replay),
                          ("the last event of a mission edited and its hash stripped", strip_last_of_mission), ("a whole mission edited and unchained", strip_whole_mission)):
            rep, _ = load(mut)
            detected = (not rep.ok) or rep.unchained > 0
            r.check(detected, f"{name}: detected by the chain alone", f"report {rep}")
        # not visible to the chain alone; an anchored head must catch them, and the limit is recorded
        for name, mut in (("the tail truncated", truncate_tail), ("a valid-looking event appended by a writer", forged_extension), ("a consistent rewrite from an edited event on", consistent_rewrite)):
            rep, hd = load(mut)
            chain_alone = (not rep.ok) or rep.unchained > 0
            anchored = hd != heads
            r.check(chain_alone or anchored, f"{name}: detected by the chain or by the anchored head", f"report {rep}; heads unchanged")
            if not chain_alone:
                r.limits.append(f"{name}: not detected by the hash chain alone (detected by comparing the head with an anchor kept outside the log's writer's reach)")
        # the anchor itself: a head recorded in a receipt must be what the store reports
        r.check(all(len(h) == 64 for h in heads.values()), "each mission exposes a 64-hex chain head to anchor")
    r.meta = {"events": len(seq), "bug": bug or None, "enterprise_store": "not exercised here: the enterprise overlay carries a separate store; see the enterprise track"}
    return r


# ─────────────────────────────── network isolation ───────────────────────────────

def _docker(*args: str, timeout: int = 120, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=check)


NETWORKY = re.compile(r"no such host|dial tcp|timeout|timed out|TLS|rate limit|toomanyrequests|unauthorized|connection refused|temporary failure|EOF|Cannot connect to the Docker daemon|network is unreachable", re.I)

PROBE = r'''
import json, socket, sys
PROXY = ("egress", 3128)
res = {}
def direct(name, host, port):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); res[name] = "REACHED"
    except Exception as e:
        res[name] = "blocked"
    finally:
        s.close()
def raw(name, payload, then=None):
    s = socket.socket(); s.settimeout(6)
    try:
        s.connect(PROXY); s.sendall(payload)
        data = s.recv(4096).decode("latin1")
        status = data.split("\r\n", 1)[0]
        res[name] = status
        if then and " 200" in status:
            s.sendall(then); s.settimeout(4); s.recv(4096)
    except Exception as e:
        res[name] = "error:" + type(e).__name__
    finally:
        s.close()
ORIGIN_IP = sys.argv[1]
direct("direct_origin_ip", ORIGIN_IP, 8080)
direct("direct_origin_name", "origin", 8080)
direct("direct_internet", "1.1.1.1", 443)
direct("direct_metadata", "169.254.169.254", 80)
try:
    socket.gethostbyname("example.com"); res["dns_external"] = "RESOLVED"
except Exception:
    res["dns_external"] = "blocked"
def get(path, host="origin:8080", uri=None):
    return ("GET " + (uri or ("http://" + host + path)) + " HTTP/1.1\r\nHost: " + host + "\r\nConnection: close\r\n\r\n").encode()
def connect(target, inner=None):
    return ("CONNECT " + target + " HTTP/1.1\r\nHost: " + target + "\r\n\r\n").encode()
raw("via_allowed_get", get("/p-allowed"))
raw("via_allowed_connect", connect("origin:8080"), then=b"GET /p-connect HTTP/1.1\r\nHost: origin\r\nConnection: close\r\n\r\n")
raw("via_denied_host", get("/p-denied-host", host="other.example.com"))
raw("via_metadata", get("/p-meta", host="169.254.169.254"))
raw("via_literal_ip_unlisted", get("/p-ip", host=ORIGIN_IP + ":8080"))
raw("via_wrong_port_connect", connect("origin:22"))
raw("via_internet_connect", connect("1.1.1.1:443"))
raw("via_lookalike", get("/p-look", host="origin.evil.test:8080"))
raw("via_userinfo", get("/p-userinfo", uri="http://origin:8080@evil.example/p-userinfo", host="origin:8080"))
raw("via_host_header_mismatch", get("/p-hdr", host="origin:8080", uri="http://other.example/p-hdr"))
raw("via_port_on_listed_host", get("/p-port", host="origin:9999"))
print(json.dumps(res))
'''


def check_network_isolation(agentic_os: str) -> R:
    r = R("network_isolation")
    bug = os.environ.get("RA_NETWORK_BUG", "")
    if not shutil.which("docker"):
        return r.not_tested("no docker CLI on this machine")
    try:
        if _docker("info", "--format", "{{.ServerVersion}}", timeout=30).returncode != 0:
            return r.not_tested("no reachable Docker daemon")
    except Exception as e:  # noqa: BLE001
        return r.not_tested(f"docker did not answer: {type(e).__name__}")
    ctx = Path(agentic_os) / "deploy" / "egress"
    if not (ctx / "Dockerfile").exists():
        r.check(False, "the repository ships its egress proxy (deploy/egress/Dockerfile)", "not found")
        return r
    T = f"ra-net-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    INT, OUT, IMG = f"{T}-int", f"{T}-out", f"{T}-img"
    mine = {"containers": [f"{T}-origin", f"{T}-proxy"], "networks": [INT, OUT], "images": [IMG]}
    allow = Path(tempfile.mkdtemp()) / "allowlist.txt"
    allow.write_text("origin:8080 private\n")
    try:
        b = _docker("build", "-q", "-t", IMG, str(ctx), timeout=600)
        if b.returncode != 0:
            tail = (b.stderr or b.stdout)[-300:]
            if NETWORKY.search(tail):
                return r.not_tested(f"the egress image could not be built here (base image not reachable): {tail.strip()[-160:]}")
            r.check(False, "the repository's egress image builds", tail)
            return r
        _docker("network", "create", *(() if bug == "open_network" else ("--internal",)), INT, check=True)
        _docker("network", "create", OUT, check=True)
        _docker("run", "-d", "--name", f"{T}-origin", "--network", OUT, "--network-alias", "origin", "--entrypoint", "python", IMG, "-u", "-m", "http.server", "8080", check=True)
        _docker("run", "-d", "--name", f"{T}-proxy", "--network", INT, "--network-alias", "egress", "-v", f"{allow}:/etc/egress/allowlist.txt:ro", IMG, check=True)
        _docker("network", "connect", OUT, f"{T}-proxy", check=True)
        time.sleep(2.5)
        oip = _docker("inspect", "-f", "{{(index .NetworkSettings.Networks \"%s\").IPAddress}}" % OUT, f"{T}-origin").stdout.strip()
        r.check(bool(oip), "the origin has an address on the outside network", "no address")
        # positive control: a client on the OUTSIDE network reaches the origin directly. Without it, "blocked" could just mean the probe cannot reach anything.
        pc = _docker("run", "--rm", "--network", OUT, "--entrypoint", "python", IMG, "-c",
                     f"import socket; s=socket.socket(); s.settimeout(3); s.connect(('{oip}',8080)); print('REACHED')", timeout=60)
        r.check("REACHED" in pc.stdout, "positive control: a client on the outside network does reach the origin directly", (pc.stdout + pc.stderr)[-200:])
        pr = _docker("run", "--rm", "--network", INT, "--entrypoint", "python", IMG, "-c", PROBE, oip, timeout=180)
        try:
            res = json.loads(pr.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            r.check(False, "the probe ran on the internal network", (pr.stdout + pr.stderr)[-300:])
            return r
        for k in ("direct_origin_ip", "direct_origin_name", "direct_internet", "direct_metadata"):
            r.check(res.get(k) == "blocked", f"a process that ignores the proxy cannot connect: {k}", res.get(k, "missing"))
        r.check(res.get("dns_external") == "blocked", "an internal-network process cannot resolve an external name", res.get("dns_external", "missing"))
        for k in ("via_allowed_get", "via_allowed_connect"):
            r.check(any(c in res.get(k, "") for c in (" 200", " 404")), f"an allowlisted destination is reachable through the proxy (the origin answers; a 404 is the origin's own): {k}", res.get(k, "missing"))
        for k in ("via_denied_host", "via_metadata", "via_literal_ip_unlisted", "via_wrong_port_connect", "via_internet_connect", "via_lookalike", "via_userinfo", "via_host_header_mismatch", "via_port_on_listed_host"):
            r.check(" 403" in res.get(k, "") or res.get(k, "").startswith("error:"), f"the proxy refuses: {k}", res.get(k, "missing"))
            r.check(" 200" not in res.get(k, ""), f"the proxy did not open a tunnel or forward: {k}", res.get(k, ""))
        # the oracle: what the ORIGIN received
        seen = _docker("logs", f"{T}-origin").stdout + _docker("logs", f"{T}-origin").stderr
        paths = set(re.findall(r'"(?:GET|CONNECT) (\S+) HTTP', seen))
        for ok_path in ("/p-allowed", "/p-connect"):
            r.check(ok_path in paths, f"the origin received the allowed request {ok_path}", f"origin saw {sorted(paths)}")
        leaked = sorted(p for p in paths if p.startswith("/p-") and p not in ("/p-allowed", "/p-connect"))
        r.check(not leaked, "the origin received no request that the proxy or the network should have stopped", f"origin saw {leaked}")
        logs = _docker("logs", f"{T}-proxy")
        plog = logs.stdout + logs.stderr
        decisions = [json.loads(x) for x in plog.splitlines() if x.startswith("{") and '"allow"' in x]
        r.check(any(d.get("allow") for d in decisions) and any(not d.get("allow") for d in decisions), "the proxy logged both allowed and refused decisions", f"{len(decisions)} decisions")
        denied_hosts = {str(d.get("host")) for d in decisions if not d.get("allow")}
        for h in ("other.example.com", "169.254.169.254", "origin.evil.test", "1.1.1.1"):
            r.check(any(h in x for x in denied_hosts), f"a refusal for {h} is on the proxy's log", f"refused: {sorted(denied_hosts)}")
        r.meta = {"probes": res, "decisions_logged": len(decisions), "origin_paths_seen": sorted(paths), "bug": bug or None}
    finally:
        for c in mine["containers"]:
            _docker("rm", "-f", c)
        for n in mine["networks"]:
            _docker("network", "rm", n)
        for i in mine["images"]:
            _docker("rmi", i)
        shutil.rmtree(allow.parent, ignore_errors=True)
    # the Kubernetes policy: static facts only. Whether a cluster ENFORCES it needs a policy-enforcing CNI.
    pol = Path(agentic_os) / "deploy" / "k8s" / "networkpolicy-egress.yaml"
    k8s = _k8s_static(pol)
    for name, ok, d in k8s:
        r.check(ok, f"kubernetes policy (static): {name}", d)
    r.limits.append("kubernetes NetworkPolicy enforcement was not exercised: that needs a cluster with a policy-enforcing CNI; the manifest was checked statically only")
    r.meta["k8s_enforcement"] = "not_tested"
    return r


def _k8s_static(path: Path) -> list:
    try:
        text = path.read_text()
    except OSError:
        return [("the manifest exists", False, str(path))]
    docs = [d for d in re.split(r"^---\s*$", text, flags=re.M) if "kind: NetworkPolicy" in d]
    names = re.findall(r"metadata:\s*\{name:\s*([\w-]+)", "\n".join(docs))
    deny_all = [d for d in docs if re.search(r"podSelector:\s*\{\}", d) and re.search(r"policyTypes:\s*\[Egress\]", d) and "egress:" not in d]
    out = [("a default-deny egress policy selects every pod and allows nothing", bool(deny_all), "none"),
           ("only the egress proxy pods get an open (0.0.0.0/0) rule", all(("app: egress-proxy" in d) for d in docs if "0.0.0.0/0" in d) and any("0.0.0.0/0" in d for d in docs), "an open rule is on another pod"),
           ("the open rule excludes link-local (metadata), CGNAT and the private ranges", all(all(x in d for x in ("169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")) for d in docs if "0.0.0.0/0" in d), "a range is missing from `except`"),
           ("policies were found", len(names) >= 3, f"{names}")]
    return out


CHECKS = {"network_isolation": check_network_isolation, "image_pinning": check_image_pinning, "log_chain": check_log_chain}


def run(agentic_os: str, only: "list | None" = None, write_baseline: bool = False) -> dict:
    out = {}
    for name, fn in CHECKS.items():
        if only and name not in only:
            continue
        try:
            res = fn(agentic_os, write_baseline) if name == "image_pinning" else fn(agentic_os)
            out[name] = res.as_dict()
        except Exception as e:  # noqa: BLE001 - a check that crashed is an error, never a pass
            out[name] = {"id": name, "status": "error", "reason": f"{type(e).__name__}: {e}"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentic-os", required=True)
    ap.add_argument("--only")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = run(a.agentic_os, a.only.split(",") if a.only else None, a.write_baseline)
    for n, r in res.items():
        extra = f" ({r.get('reason')})" if r.get("reason") else ""
        print(f"{n:20} {r['status']:11} checks={r.get('checks', '-')}{extra}")
        for f in r.get("failed", [])[:5]:
            print(f"    FAILED {f['name']}: {f['detail'][:160]}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    st = [r["status"] for r in res.values()]
    return 2 if "error" in st else 1 if FAIL in st else 0


if __name__ == "__main__":
    raise SystemExit(main())
