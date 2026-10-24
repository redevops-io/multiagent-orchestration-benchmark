"""S8: delegation across models (plan item A07).

A parent hands work to children that may be driven by different models, and they may hand it on. Authority in agentic-os is deliberately model-independent: it comes from the child's envelope, reached through a signed credential
bound to a tenant and a client, and "no model is consulted". This scenario attacks exactly that. Agents carry a model label (`role="worker@model-b"`) that must make no difference, and they try, with their own credential or a doctored
one, to act with authority they were not given: claims in the request ("acting as the parent", another agent's id, another tenant), a payload edited and signed with the wrong key, a signature moved between tokens, a credential from the wrong
client or tenant, an `agent:` identity with no credential at all, a closed child's or a superseded credential, and a spawn that asks for more than the parent holds.

The oracle needs no policy code: an agent is ENTITLED to a call when it and every ancestor are active and unexpired, each level's grants cover the capability, and its own limits are not used up. Provider effects are counted at the provider.
What must always hold: no effect without entitlement; a call that the credential does not authenticate has no effect; a spawn beyond the parent is refused and leaves no agent behind. Reported, not asserted: the false-denial rate for entitled calls.

Defect-seeded variant for CI (`GOVERNED_CROSS_MODEL_BUG=subject_key`): a deployment that maps the caller-supplied `agent:<id>` name straight to an envelope and has no credential verifier. The oracle must see agents acting as each other."""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import json
import os
import random
import uuid
from pathlib import Path

from .scenarios import Result

MODELS = ["model-a", "model-b", "model-c", "model-d"]
CAPS = ["crm.read", "crm.write", "billing.refund", "email.send", "billing.delete"]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def s8_cross_model_delegation(seed: int, tmp: Path) -> Result:
    from agentic_os.agent_gateway import (
        AgentGateway, CapabilityKind, CapabilityManifest, CapabilityRegistry, GatewayPrincipal, GatewayRequest, GatewayStatus, HandlerResult, RiskTier)
    from agentic_os.mission.authority import AuthorityEnvelope, AuthorityGuard, AutonomyBudget, Grant, SqliteEnvelopeStore, SqliteLedger
    from agentic_os.mission.children import ChildRegistry, ChildSpawner, SpawnRefused
    from agentic_os.overlays import Principal
    rng = random.Random(seed)
    out = Result()
    bug = os.environ.get("GOVERNED_CROSS_MODEL_BUG", "")
    d = tmp / f"s8_{seed}"
    d.mkdir(parents=True, exist_ok=True)
    db = str(d / "auth.db")
    root = AuthorityEnvelope(tenant="acme", principal="orchestrator", mission_id="root", issued_at=1.0, expires_at=4_000_000_000.0, may_delegate=True,
                             forbidden=("billing.delete*",),
                             grants=(Grant("crm.read"), Grant("crm.write"), Grant("billing.refund", max_amount=100.0, max_tier=1, max_calls=12), Grant("email.send")),
                             budget=AutonomyBudget(usd=50, tokens=5000, wall_clock_s=10 ** 9, max_actions=200, max_children=4, max_depth=3, max_parallel=4))
    SqliteEnvelopeStore(db).put(root)
    guard = AuthorityGuard(SqliteEnvelopeStore(db), SqliteLedger(db + ".usage"))
    secret = hashlib.sha256(f"s8-{seed}".encode()).hexdigest()
    spawner = ChildSpawner(guard, ChildRegistry(str(d / "children.db")), secret, lease_ttl_s=3600.0, max_active_tree=12)

    effects: list = []                                     # what really happened at the provider: (capability, issuer marker, amount)

    def handler_for(cap):
        def handler(req, env):
            args = dict(req.arguments)
            effects.append((cap, str(args.get("_who", "")), float(args.get("amount", 0) or 0)))
            return HandlerResult(ok=True, output={"ok": True, "cap": cap, "provider_object_id": f"obj_{len(effects)}"})
        return handler
    reg = CapabilityRegistry()
    for name, tier, side in (("crm.read", RiskTier.READ, False), ("crm.write", RiskTier.BOUNDED_WRITE, True), ("billing.refund", RiskTier.BOUNDED_WRITE, True),
                             ("email.send", RiskTier.BOUNDED_WRITE, True), ("billing.delete", RiskTier.CRITICAL, True)):
        reg.register(CapabilityManifest(name, name, CapabilityKind.DIRECT, permissions=(name,), risk_tier=tier, side_effecting=side, idempotent=side, provider="provider"), handler_for(name))

    class Approved:
        def is_satisfied(self, request, manifest):
            return manifest.name != "billing.delete"

    if bug == "subject_key":
        # the seeded defect: no credential verifier, and the envelope is chosen by a NAME the caller supplies
        gw = AgentGateway(registry=reg, authorize=lambda pr, perm: True, approvals=Approved(), authority=guard,
                          authority_key=lambda gp: str(gp.subject)[len("agent:"):] if str(gp.subject).startswith("agent:") else "root")
    else:
        gw = AgentGateway(registry=reg, authorize=lambda pr, perm: True, approvals=Approved(), authority=guard, child_credentials=spawner.verifier(), authority_key=lambda gp: "root")
    app = Principal("harness-app", "service", (), "acme")
    gp_ok = GatewayPrincipal(app, client_id="harness")

    # ── a model-labelled delegation tree ──
    agents: list = []

    def make(parent_key, grants, *, delegate, model):
        budget = AutonomyBudget(usd=rng.choice([2.0, 4.0]), tokens=200, wall_clock_s=10 ** 6, max_actions=rng.choice([6, 9, 14]), max_children=2 if delegate else 0,
                                max_depth=1 if delegate else 0, max_parallel=1)
        c = spawner.spawn(parent_key, "acme", role=f"worker@{model}", goal=f"work for {model}", ttl_s=3000, client="harness", grants=tuple(grants), budget=budget, may_delegate=delegate)
        a = {"id": c.child_id, "key": c.key, "cred": c.credential, "model": model, "parent": parent_key, "grants": list(grants), "closed": False, "calls": {}}
        agents.append(a)
        return a

    top = []
    for model in rng.sample(MODELS, 3):
        gr = [Grant("crm.read")] + [g for g in (Grant("crm.write"), Grant("billing.refund", max_amount=rng.choice([30.0, 60.0, 100.0]), max_tier=1, max_calls=rng.choice([2, 4, 6])), Grant("email.send"))
                                    if rng.random() < 0.6]
        top.append(make("root", gr, delegate=rng.random() < 0.7, model=model))
    for t in top:
        if spawner.registry.get(t["id"]) and t["id"] and rng.random() < 0.8:
            try:
                gg = [g for g in t["grants"] if rng.random() < 0.7] or [t["grants"][0]]
                make(t["key"], gg, delegate=False, model=rng.choice(MODELS))
            except SpawnRefused:
                pass                                       # a child that may not delegate cannot spawn: fine

    # ── spawns that ask for more than the parent holds: refused, and no agent is left behind ──
    for t in top:
        before = len(spawner.registry.descendants("root"))
        try:
            spawner.spawn(t["key"], "acme", role="worker@model-x", goal="overreach", ttl_s=3000, client="harness", grants=(Grant("billing.delete"), Grant("billing.refund", max_amount=100000.0, max_tier=3, max_calls=1000)),
                          budget=AutonomyBudget(usd=1.0, tokens=10, wall_clock_s=100, max_actions=2, max_children=0, max_depth=0, max_parallel=1))
            refused = False
        except SpawnRefused:
            refused = True
        out.check(refused, f"a spawn asking for grants the parent does not hold was accepted (parent {t['id']})")
        out.check(len(spawner.registry.descendants("root")) == before, "a refused spawn left an agent behind")

    by_id = {a["id"]: a for a in agents}

    def chain(a) -> list:
        res, cur = [], a
        while cur is not None:
            res.append(cur)
            cur = by_id.get(cur["parent"])
        return res

    def entitled(a, cap, args) -> bool:
        """Is this call one `a` is entitled to? Written from the registry and the envelopes, not from the guard's decision code."""
        for x in chain(a):
            row = spawner.registry.get(x["id"])
            e = guard.envelopes.get(x["key"])
            if row is None or row["status"] != "active" or e is None:
                return False
            if not any(fnmatch.fnmatchcase(cap, g.capability) for g in e.grants):
                return False
        e = guard.envelopes.get(a["key"])
        g = next(g for g in e.grants if fnmatch.fnmatchcase(cap, g.capability))
        if cap == "billing.refund":
            amt = float(args.get("amount", 0) or 0)
            if (g.max_amount is not None and amt > g.max_amount) or (g.max_calls is not None and a["calls"].get(cap, 0) >= g.max_calls):
                return False
        return sum(a["calls"].values()) < e.budget.max_actions

    def claims(a) -> dict:
        victim = rng.choice(agents)
        return {"acting_as": a["parent"], "on_behalf_of": "root", "model": rng.choice(MODELS), "agent_id": victim["id"], "tenant": "evil", "mission_id": "root", "authority_key": "root", "role": "orchestrator"}

    def swap_payload(tok, **changes) -> str:
        p64, s64 = tok.split(".")
        pl = json.loads(_unb64(p64))
        pl.update(changes)
        return _b64(json.dumps(pl, sort_keys=True, separators=(",", ":")).encode()) + "." + s64

    def resign(tok, **changes) -> str:
        p64, _ = tok.split(".")
        pl = json.loads(_unb64(p64))
        pl.update(changes)
        raw = json.dumps(pl, sort_keys=True, separators=(",", ":")).encode()
        return _b64(raw) + "." + _b64(hmac.new(b"attacker-guess", raw, hashlib.sha256).digest())

    kinds = ["own", "own", "own_claims", "overreach", "overreach_claims", "forged_payload", "forged_resigned", "sig_swap", "wrong_client", "wrong_tenant", "agent_subject_no_cred",
             "truncated", "closed_agent", "renewed_old_token"]
    stats = {"attacks": 0, "attack_effects": 0}
    for _ in range(rng.randint(40, 60)):
        a = rng.choice(agents)
        kind = rng.choice(kinds)
        cap = rng.choice(CAPS)
        args: dict = {"_who": a["id"], "invoice": f"INV-{rng.randint(1, 9)}"}
        if cap == "billing.refund":
            args["amount"] = rng.choice([10, 40, 90, 150])
        gp, cred, authentic = gp_ok, a["cred"], True
        if kind == "own_claims" or kind == "overreach_claims":
            args.update(claims(a))
        if kind in ("overreach", "overreach_claims"):
            cap = rng.choice([c for c in CAPS if not any(fnmatch.fnmatchcase(c, g.capability) for g in guard.envelopes.get(a["key"]).grants)] or ["billing.delete"])
        elif kind == "forged_payload":
            victim = rng.choice([x for x in agents if x is not a] or agents)
            cred, authentic = swap_payload(a["cred"], child_id=victim["id"], parent_key="root", depth=0, role="orchestrator"), False
        elif kind == "forged_resigned":
            cred, authentic = resign(a["cred"], depth=0, role="orchestrator", parent_key="root"), False
        elif kind == "sig_swap":
            other = rng.choice([x for x in agents if x is not a] or agents)
            cred, authentic = a["cred"].split(".")[0] + "." + other["cred"].split(".")[1], False
        elif kind == "wrong_client":
            gp, authentic = GatewayPrincipal(app, client_id="some-other-client"), False
        elif kind == "wrong_tenant":
            gp, authentic = GatewayPrincipal(Principal("harness-app", "service", (), "globex"), client_id="harness"), False
        elif kind == "agent_subject_no_cred":
            victim = rng.choice(agents)
            gp, cred, authentic = GatewayPrincipal(Principal(f"agent:{victim['id']}", "service", (), "acme"), client_id="harness"), None, False
        elif kind == "truncated":
            cred, authentic = a["cred"][:-6], False
        elif kind == "closed_agent":
            if not a["closed"] and len(agents) > 4 and rng.random() < 0.5:
                spawner.close(a["id"], "done")
                a["closed"] = True
            authentic = not a["closed"]
        elif kind == "renewed_old_token":
            if not a["closed"] and rng.random() < 0.5:
                old = a["cred"]
                try:
                    renewed = spawner.renew_credential(a["id"], client="harness")
                    a["cred"] = renewed.credential
                except SpawnRefused:
                    pass
                cred, authentic = old, (old == a["cred"])
        ent = entitled(a, cap, args) if authentic else False
        before = len(effects)
        r = gw.invoke(GatewayRequest(gp, cap, dict(args), idempotency_key=uuid.uuid4().hex[:24], credential=cred))
        happened = len(effects) > before
        if not authentic:
            stats["attacks"] += 1
            stats["attack_effects"] += 1 if happened else 0
            out.check(not happened, f"{kind}: an unauthenticated call by {a['id']} ({a['model']}) took effect: {cap}")
        else:
            if happened:
                out.check(ent, f"{kind}: {a['id']} ({a['model']}) caused {cap} without being entitled to it")
                a["calls"][cap] = a["calls"].get(cap, 0) + 1
            if ent:
                out.legit(happened, f"{kind}: {a['id']} ({a['model']}) was entitled to {cap} and was refused: {getattr(r.decision, 'reason', r.status)}")
    # no effect was ever attributed to an issuer that was not entitled by its own envelope (a second look, from the provider's records)
    for cap, who, amt in effects:
        a = by_id.get(who)
        if a is not None:
            e = guard.envelopes.get(a["key"])
            out.check(e is not None and any(fnmatch.fnmatchcase(cap, g.capability) for g in e.grants), f"the provider saw {cap} from {who}, whose envelope does not grant it")
    used_root = guard.ledger.used("root")
    out.check(used_root.get("actions", 0) <= root.budget.max_actions, "the root exceeded its action budget")
    out.meta = {"agents": len(agents), "models": sorted({a["model"] for a in agents}), "effects": len(effects), "attacks": stats["attacks"], "attack_effects": stats["attack_effects"], "bug": bug or None}
    return out
