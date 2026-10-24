"""S7: reconciling an unknown outcome against a provider's own records.

A side-effecting call times out after the request was written. Whether the provider applied it is unknown, and the providers give little to go on: Lago, Gmail, Postiz
and Apollo have no idempotency lookup, and their records are not guaranteed to be visible at once. The scenario runs the real `agentic_os` runtime, reconciliation supervisor and
provider probes (`mission/probe_adapters`) against simulated providers whose state is the ground truth, with seeded faults:

  * the effect applied, or the request was lost before it applied (the caller sees a timeout either way);
  * the record is hidden for the first probe rounds (eventual consistency), and lookups fail transiently (429 with Retry-After, 503, a timeout);
  * an UNRELATED record with the same fields exists (a person refunded the same invoice, a colleague sent the same subject to the same recipient, a post with the same text);
  * a stale record from before the call; the provider keeps or drops a client-supplied Message-ID;
  * the supervisor process dies in the middle of a probe round and a new one takes over the same files.

The oracle is written from the ground truth the simulator holds, not from the adapters. What must ALWAYS hold:

  O1  nothing is settled as executed unless the effect really is in the provider (no false confirmation);
  O2  nothing is ever released as not executed (no adapter here can prove absence), and the business call is never sent again while the outcome is unknown;
  O3  a provider that cannot tie a record to our request (a refund, a sent email without a Message-ID, a post) never settles on a lookalike by default: it escalates with the candidate;
  O4  where the provider DOES identify the effect (a subscription by external_id, an enrollment by contact and sequence, an email by a Message-ID it kept) and the record becomes visible
      within the retry budget, the reservation is settled as executed without a person;
  O5  an unresolvable job ends escalated exactly once, never stuck pending;
  O6  probing only reads: the provider sees exactly one write, the original;
  O7  no secret from the call's inputs or the probe transport's credential is written into the reservation, the job table or the reconciliation events;
  O8  a supervisor crash in the middle of a probe round changes nothing about the outcome and does not reset the attempts."""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time
from pathlib import Path

from . import kit
from .scenarios import EVIDENCE, Result  # noqa: F401

KINDS = ["lago_subscription", "lago_refund", "gmail", "gmail_message_id", "postiz", "apollo"]
CANDIDATE_ONLY = {"lago_refund", "gmail", "postiz"}              # a lookalike cannot be told from ours
CAPS = {"lago_subscription": "billing.create_subscription", "lago_refund": "billing.refund", "gmail": "email.message.send", "gmail_message_id": "email.message.send",
        "postiz": "content.publish", "apollo": "outreach.enroll"}
SECRET_INPUT = "SECRET-INPUT-7f3a9c1e"
SECRET_BODY = "SECRET-BODY-41d0b8aa"
SECRET_TRANSPORT = "SECRET-TRANSPORT-c92e55b0"


class World:
    """One simulated provider. `records` is the provider's state; `ours` the ids our write created (ground truth); every lookup is a GET."""

    def __init__(self, kind: str, rng: random.Random, *, hidden: int, faults: int, keeps_msgid: bool) -> None:
        self.kind, self.rng, self.hidden, self.faults, self.keeps_msgid = kind, rng, hidden, faults, keeps_msgid
        self.records: list = []
        self.ours: set = set()
        self.writes = 0
        self.reads = 0
        self.round = 0
        self.rejected_writes = 0
        self.inputs: dict = {}
        self.write_applies = True

    # ── the write path (what the mission's call does) ──
    def invoke(self, operator, capability, inputs, idempotency_key, *, secrets=None):
        self.writes += 1
        self.inputs = dict(inputs)
        if self.write_applies:
            self._apply(capability, inputs)
        raise TimeoutError("timed out after the request was written")        # the caller never learns the outcome, applied or not

    def _apply(self, cap: str, i: dict) -> None:
        now = time.time()
        n = len(self.records) + 1
        if self.kind == "lago_subscription":
            self.records.append({"id": f"sub{n}", "external_id": _ext(i["customer"], i["plan_code"]), "status": "active", "created": now, "ours": True})
        elif self.kind == "lago_refund":
            self.records.append({"id": f"cn{n}", "invoice": i["invoice_number"], "cents": _cents(i["amount"]), "created": now, "ours": True})
        elif self.kind in ("gmail", "gmail_message_id"):
            self.records.append({"id": f"m{n}", "to": i["to"], "subject": i["subject"], "created": now, "msgid": i.get("message_id") if self.keeps_msgid else f"gen{n}@mail", "ours": True})
        elif self.kind == "postiz":
            for integ in i["integrations"]:
                self.records.append({"id": f"p{n}-{integ}", "integration": integ, "content": f"<p>{i['content']}</p>", "date": now, "state": "PUBLISHED", "ours": True})
        elif self.kind == "apollo":
            for cid in i["contact_ids"]:
                self.records.append({"id": cid, "seq": i["sequence_id"], "added": now, "ours": True})
        self.ours = {r["id"] for r in self.records if r.get("ours")}

    def lookalike(self, i: dict, *, at: float, stale: bool = False) -> None:
        """A record that is not ours but looks like it (same fields). `stale`: from long before the call."""
        n = len(self.records) + 1
        t = at - 86400 if stale else at
        if self.kind == "lago_refund":
            self.records.append({"id": f"cn{n}", "invoice": i["invoice_number"], "cents": _cents(i["amount"]), "created": t})
        elif self.kind in ("gmail", "gmail_message_id"):
            self.records.append({"id": f"m{n}", "to": i["to"], "subject": i["subject"], "created": t, "msgid": f"other{n}@mail"})
        elif self.kind == "postiz":
            self.records.append({"id": f"p{n}", "integration": i["integrations"][0], "content": f"<p>{i['content']}</p>", "date": t, "state": "PUBLISHED"})
        elif self.kind == "apollo":
            self.records.append({"id": i["contact_ids"][0], "seq": i["sequence_id"], "added": t})

    # ── the read path (what a probe's transport does) ──
    def _visible(self, r: dict) -> bool:
        return (self.round > self.hidden) if r.get("ours") else True

    def http(self, path: str, params: dict):
        self.reads += 1
        if self.round <= self.faults:                                            # transient trouble for the first rounds
            f = (self.round + self.rng.randrange(3)) % 3
            if f == 0:
                return 429, None, {"Retry-After": "5"}
            if f == 1:
                return 503, None, {}
            raise TimeoutError("lookup timed out")
        vis = [r for r in self.records if self._visible(r)]
        if self.kind == "lago_subscription":
            ext = path.rsplit("/", 1)[1]
            st = params.get("status", "active")
            for r in vis:
                if r["external_id"] == ext and r["status"] == st:
                    return 200, {"subscription": {"lago_id": r["id"], "external_id": ext, "status": st, "created_at": _iso(r["created"])}}, {}
            return 404, {"error": "not found"}, {}
        if self.kind == "lago_refund":
            rows = [{"lago_id": r["id"], "number": f"CN-{r['id']}", "refund_amount_cents": r["cents"], "created_at": _iso(r["created"]), "refund_status": "succeeded"}
                    for r in vis if r["invoice"] == params.get("invoice_number")]
            return 200, {"credit_notes": rows}, {}
        if self.kind in ("gmail", "gmail_message_id"):
            if path == "/users/me/messages":
                q = str(params.get("q", ""))
                if q.startswith("rfc822msgid:"):
                    mid = q.split(":", 1)[1]
                    hit = [r for r in vis if r["msgid"].strip("<>") == mid]
                else:
                    after = float(q.rsplit("after:", 1)[1]) if "after:" in q else 0.0
                    hit = [r for r in vis if f"to:{r['to']}" in q and f'subject:"{r["subject"]}"' in q and r["created"] >= after]
                return 200, {"messages": [{"id": r["id"]} for r in hit]}, {}
            mid = path.rsplit("/", 1)[1]
            for r in vis:
                if r["id"] == mid:
                    return 200, {"id": r["id"], "threadId": "t" + r["id"], "internalDate": str(int(r["created"] * 1000)),
                                 "payload": {"headers": [{"name": "Subject", "value": r["subject"]}, {"name": "To", "value": r["to"]}, {"name": "Message-ID", "value": f"<{r['msgid']}>"}]}}, {}
            return 404, {}, {}
        if self.kind == "postiz":
            lo, hi = params["startDate"], params["endDate"]
            posts = [{"id": r["id"], "content": r["content"], "publishDate": _iso(r["date"]), "state": r["state"], "error": None, "integration": {"id": r["integration"]}}
                     for r in vis if lo <= _iso(r["date"]) <= hi]
            return 200, {"posts": posts}, {}
        if self.kind == "apollo":
            cid = path.rsplit("/", 1)[1]
            mine = [r for r in vis if r["id"] == cid]
            if not mine:
                return 404, {}, {}
            return 200, {"contact": {"id": cid, "emailer_campaign_ids": [r["seq"] for r in mine],
                                     "contact_campaign_statuses": [{"status": "active", "added_at": _iso(r["added"]), "emailer_campaign_id": r["seq"]} for r in mine]}}, {}
        return 404, {}, {}


def _ext(customer: str, plan: str) -> str:
    import re
    return "sub-" + re.sub(r"[^a-z0-9]+", "-", f"{customer}-{plan}".lower()).strip("-")


def _cents(amount) -> int:
    return int(round(float(str(amount).lstrip("$")) * 100))


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _inputs(kind: str, seed: int) -> dict:
    base = {"api_key": SECRET_INPUT, "access_token": SECRET_INPUT}
    if kind == "lago_subscription":
        return {**base, "customer": f"acme-{seed}", "plan_code": "pro"}
    if kind == "lago_refund":
        return {**base, "invoice_number": f"INV-{1000 + seed}", "amount": f"{10 + seed % 7}.50"}
    if kind in ("gmail", "gmail_message_id"):
        d = {**base, "to": f"buyer{seed}@example.com", "subject": f"Your invoice {seed}", "body": SECRET_BODY}
        if kind == "gmail_message_id":
            d["message_id"] = f"req-{seed}@redevops"
        return d
    if kind == "postiz":
        return {**base, "content": f"Launch day {seed}: we shipped", "integrations": ["chan1", "chan2"][: 1 + seed % 2], "type": "now"}
    return {**base, "contact_ids": [f"contact{seed}"], "sequence_id": f"seq{seed}"}


def _provider_registry():
    from agentic_os.mission.registry import CapabilityRegistry
    from agentic_os.mission.types import CapabilityManifest, CapabilitySpec, NodeCost
    reg = CapabilityRegistry()
    caps = [CapabilitySpec(c, "provider-op", provides=[f"out_{i}"], side_effecting=True, undo="w.undo", compensation={"supported": True, "capability": "w.undo", "verify": "w.verify"},
                           permissions=["w:write"], cost=NodeCost(usd=0.1)) for i, c in enumerate(sorted(set(CAPS.values())))]
    caps += [CapabilitySpec("w.verify", "provider-op", permissions=["w:write"]), CapabilitySpec("w.undo", "provider-op", permissions=["w:write"])]
    reg.register(CapabilityManifest("provider-op", caps))
    return reg


def s7_provider_reconciliation(seed: int, tmp: Path) -> Result:
    from agentic_os.mission.executor import Executor
    from agentic_os.mission.fork_join import BranchSpec, ForkSpec, JoinSpec, SimulatedCrash
    from agentic_os.mission.probe_adapters import apollo, gmail, lago, postiz
    from agentic_os.mission.probes import ProbeRegistry
    from agentic_os.mission.reconcile_jobs import ReconcileJobs, ReconciliationSupervisor, RetryPolicy
    from agentic_os.mission.runtime import MissionRuntime
    from agentic_os.mission.store import EventStore
    from agentic_os.mission.types import Budget

    naive = os.environ.get("GOVERNED_NAIVE_ADAPTERS") == "1"        # negative control: adapters that trust a unique lookalike. The oracle must catch them.
    kind = KINDS[seed % len(KINDS)]
    rng = random.Random(seed)
    executed = rng.random() < 0.55
    hidden = rng.choice([0, 0, 1, 2])
    faults = rng.choice([0, 0, 1, 2])
    keeps_msgid = rng.random() < 0.5
    lookalikes = rng.choice([0, 1, 1, 2]) if kind in CANDIDATE_ONLY | {"gmail_message_id"} else 0
    stale = kind == "apollo" and not executed and rng.random() < 0.6
    crash = rng.random() < 0.5
    out = Result(meta={"kind": kind, "executed": executed, "hidden_rounds": hidden, "transient_rounds": faults, "lookalikes": lookalikes, "stale": stale, "crash": crash,
                       "keeps_msgid": keeps_msgid, "naive": naive})
    d = tmp / f"s7_{seed}"
    d.mkdir(parents=True, exist_ok=True)

    world = World(kind, rng, hidden=hidden, faults=faults, keeps_msgid=keeps_msgid)
    world.write_applies = executed
    inputs = _inputs(kind, seed)
    cap = CAPS[kind]

    # ── the real probe adapters over the simulated provider (the transport carries a credential the oracle looks for) ──
    probes = ProbeRegistry()

    def transport(path, params):
        assert SECRET_TRANSPORT                                                   # the credential lives in this closure only
        return world.http(path, params)
    if kind.startswith("lago"):
        lago.register_lago(probes, transport, accept_unique_refund_match=naive)
    elif kind.startswith("gmail"):
        gmail.register_gmail(probes, transport, accept_unique_match=naive)
    elif kind == "postiz":
        postiz.register_postiz(probes, transport, accept_unique_match=naive)
    else:
        apollo.register_apollo(probes, transport)

    guard = kit.durable_guard(d, grants=("billing.*", "email.*", "content.*", "outreach.*", "w.*"))
    log = str(d / "events.jsonl")

    def boot():
        rt = MissionRuntime(_provider_registry(), Executor(world), store=EventStore(log), max_concurrency=1, planner=kit.StepsPlanner(), authority=guard(),
                            authority_key=lambda m: "env")
        if os.environ.get("GOVERNED_NO_EFFECT_REF") != "1":              # negative control: a runtime that does not record what identifies the effect (as before agentic-os #566)
            rt.probe_refs = probes
        rt.forks.poll_s = 0.002
        return rt

    def spec():
        return ForkSpec("f1", [BranchSpec("A", cap, inputs=inputs)], JoinSpec("all", None, None, "fail", True), max_concurrent=1)

    rt = boot()
    m = rt.create_mission("provider call|", policy_refs=["w:write"], budget=Budget(usd=100.0), tenant="acme", principal="owner", policy=kit.standing_policy())
    t_dispatch = time.time()
    res = rt.forks.run_fork(m.id, spec())
    if not out.check(res.status == "awaiting_reconciliation", f"the timed-out call did not leave the mission awaiting reconciliation ({res.status})"):
        return out
    for _ in range(lookalikes):
        world.lookalike(inputs, at=time.time() + 1)
    if stale:
        world.lookalike(inputs, at=t_dispatch, stale=True)

    # ── the supervisor, with a clock the scenario advances and (optionally) a death in the middle of a probe round ──
    class Clock:
        def __init__(self):
            self.t = time.time()

        def __call__(self):
            return self.t
    clock = Clock()
    jobs_path = str(d / "jobs.db")
    policy = RetryPolicy(max_probes=5, initial_s=30, max_s=300, factor=2.0, jitter=0.0)

    def supervisor(runtime, die_after_probe: bool = False):
        reg = probes
        if die_after_probe:
            reg = _DyingRegistry(probes)
        return ReconciliationSupervisor(runtime, ReconcileJobs(jobs_path), reg, policy=policy, owner=f"sup-{seed}", clock=clock, rng=random.Random(seed))

    def jobs():
        return ReconcileJobs(jobs_path).list()

    sup = supervisor(rt)
    sup.scan()
    attempts_before_crash = None
    crashed = False
    for r in range(1, 9):
        world.round = r
        clock.t += 400
        if crash and r == 2 and not crashed:
            # the supervisor dies right after the provider answered and before it records what it learned; a new process takes over the same files
            try:
                supervisor(rt, die_after_probe=True).tick()
            except SimulatedCrash:
                crashed = True
            attempts_before_crash = [j["attempts"] for j in jobs()]
            kit.new_process()
            rt = boot()
            rt.rehydrate(m.id, policy_refs=["w:write"])
            sup = supervisor(rt)
            sup.scan()
        sup.tick()
        if all(j["state"] in ("resolved", "escalated") for j in jobs()):
            break
    js = jobs()
    out.check(len(js) == 1, f"{len(js)} reconcile jobs for one unknown outcome")
    job = js[0] if js else {}
    resolution, state = job.get("resolution"), job.get("state")
    out.meta.update({"final_state": state, "resolution": resolution, "probe_rounds": job.get("attempts"), "crashed": crashed})
    tag = f"[{kind} executed={executed} hidden={hidden} faults={faults} lookalikes={lookalikes} stale={stale} crash={crash}{' naive' if naive else ''}]"

    # O1 no false confirmation
    truth = bool(world.ours) if kind != "lago_subscription" else any(r["external_id"] == _ext(inputs["customer"], inputs["plan_code"]) for r in world.records)
    if resolution == "executed":
        out.check(truth, f"{tag} O1 settled as executed but the effect is not in the provider")
    # O2 nothing released, nothing resent
    out.check(resolution != "not_executed", f"{tag} O2 released as not executed")
    out.check(world.writes == 1, f"{tag} O2/O6 the provider saw {world.writes} writes (the business call was sent again)")
    pend = list(rt.repo.pending_reconciliation(m.id))
    if resolution != "executed":
        out.check(bool(pend), f"{tag} O2 the reservation is not open although the job is {state}/{resolution}")
    # O3 candidate-only providers never settle on a lookalike by default
    if kind in CANDIDATE_ONLY or (kind == "gmail_message_id" and not keeps_msgid):
        out.check(resolution != "executed", f"{tag} O3 settled on a record the provider does not tie to the request")
    # O4 identified effects resolve without a person
    identified = kind in ("lago_subscription", "apollo") or (kind == "gmail_message_id" and keeps_msgid)
    budget_ok = max(hidden, faults) + 1 <= 4
    if identified and executed and budget_ok:
        out.check(resolution == "executed", f"{tag} O4 an identified, visible effect was not settled (state {state}, resolution {resolution}, detail {job.get('last_detail')!r})")
    # O5 an unresolved job ends escalated, once
    if resolution != "executed":
        out.check(state == "escalated", f"{tag} O5 an unresolved job ended {state!r}, not escalated")
        esc = [h for h in ReconcileJobs(jobs_path).history(job["job_id"]) if h["kind"] == "escalated"] if job and hasattr(ReconcileJobs, "history") else []
        if hasattr(ReconcileJobs, "history"):
            out.check(len(esc) == 1, f"{tag} O5 escalated {len(esc)} times")
    # O6 read-only
    out.check(world.reads >= 1, f"{tag} O6 the probes never read the provider")
    # O7 no secret in what the reconciliation layer wrote
    blobs = [Path(jobs_path).read_bytes().decode("utf-8", "replace")]
    for pth in (Path(jobs_path + "-wal"),):
        if pth.exists():
            blobs.append(pth.read_bytes().decode("utf-8", "replace"))
    for p in pend:
        blobs.append(json.dumps(guard().lease(p["lease_id"]) or {}, default=str))
    blobs += [json.dumps(e.payload, default=str) for e in rt.store.for_mission(m.id) if "econcil" in e.type]
    text = "\n".join(blobs)
    for s in (SECRET_INPUT, SECRET_BODY, SECRET_TRANSPORT):
        out.check(s not in text, f"{tag} O7 a secret reached the reconciliation records ({s[:14]}...)")
    # O8 the crash changed nothing
    if crashed:
        out.check(all(j["attempts"] >= (attempts_before_crash or [0])[0] for j in js), f"{tag} O8 the retry budget was reset by the restart")
        out.check(world.writes == 1, f"{tag} O8 the restart sent the business call again")
    return out


class _DyingRegistry:
    """The probe answers (the provider was read) and the process dies before the supervisor can record the answer."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def adapters_for(self, capability):
        from agentic_os.mission.fork_join import SimulatedCrash
        out = []
        for q, p in self._inner.adapters_for(capability):
            def dying(ctx, _p=p):
                _p(ctx)
                raise SimulatedCrash("died after the provider answered")
            out.append((q, dying))
        return out
