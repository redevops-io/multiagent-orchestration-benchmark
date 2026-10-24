"""Shared fixtures for the governed-properties suite: an external system that executes each idempotency key at most once and counts every
send, a registry of side-effecting and read-only capabilities, a durable authority guard and event log, and a crash simulator.

Everything runs offline: no model calls, no API keys, no network. The only dependency is an `agentic_os` checkout or install."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path


def import_agentic_os(path: "str | None" = None) -> str:
    """Make `agentic_os` importable (from `path`, $AGENTIC_OS_PATH, or an installed package) and return its git revision if known."""
    p = path or os.environ.get("AGENTIC_OS_PATH")
    if p:
        sys.path.insert(0, str(Path(p).resolve()))
    import agentic_os  # noqa: F401
    root = Path(agentic_os.__file__).resolve().parent.parent
    head = root / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            ref = (root / ".git" / ref[5:]).read_text().strip()
        return ref[:12]
    except Exception:  # noqa: BLE001
        return "installed"


class Provider:
    """The EXTERNAL system. Executes each idempotency key at most once; `calls[key]` counts every send, `effects[key]` every real effect.
    `timeout_keys`: the request is written (the effect applies when `applied_on_timeout`) and then the caller sees a timeout: UNKNOWN.
    `fail_caps`: rejected before anything is applied (clean failure, nothing to reconcile)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: dict = {}
        self.effects: dict = {}
        self.results: dict = {}
        self.timeout_keys: set = set()
        self.timeout_caps: set = set()
        self.applied_on_timeout = True
        self.fail_caps: set = set()
        self.running = 0
        self.delay = 0.0
        self.cap_of: dict = {}
        self.after_effect: dict = {}                 # key -> sends that arrived AFTER the effect had already happened

    def invoke(self, operator, capability, inputs, idempotency_key, *, secrets=None):
        with self.lock:
            self.calls[idempotency_key] = self.calls.get(idempotency_key, 0) + 1
            self.cap_of[idempotency_key] = capability
            if idempotency_key in self.effects:
                self.after_effect[idempotency_key] = self.after_effect.get(idempotency_key, 0) + 1
            if idempotency_key in self.results:
                return self.results[idempotency_key]
            self.running += 1
        try:
            if self.delay:
                time.sleep(self.delay)
            if capability in self.fail_caps:
                e = RuntimeError(f"{capability} rejected before sending")
                e.not_applied = True
                raise e
            timed_out = idempotency_key in self.timeout_keys or capability in self.timeout_caps
            with self.lock:
                if (not timed_out) or self.applied_on_timeout:
                    if idempotency_key not in self.effects:
                        self.effects[idempotency_key] = 1
                        self.results[idempotency_key] = {"ok": True, "key": idempotency_key, "provider_object_id": f"obj_{idempotency_key[-8:]}"}
            if timed_out:
                raise TimeoutError("timed out after the request was written")
            return self.results[idempotency_key]
        finally:
            with self.lock:
                self.running -= 1

    def quiesce(self, timeout: float = 5.0) -> None:
        end = time.time() + timeout
        while self.running and time.time() < end:
            time.sleep(0.005)

    def resent_after_effect(self, read_only=("w.read",)) -> list:
        """Side-effecting keys that were sent again after their effect had happened: the one thing that must never occur."""
        return [(k, n) for k, n in self.after_effect.items() if self.cap_of.get(k) not in read_only]

    def duplicate_effects(self) -> list:
        return [k for k, n in self.effects.items() if n > 1]


def registry():
    from agentic_os.mission.registry import CapabilityRegistry
    from agentic_os.mission.types import CapabilityManifest, CapabilitySpec, NodeCost
    reg = CapabilityRegistry()
    caps = [CapabilitySpec(f"w.{n}", "w-op", provides=[f"out_{n}"], side_effecting=True, undo="w.undo",
                           compensation={"supported": True, "capability": "w.undo", "verify": "w.verify"}, permissions=["w:write"],
                           cost=NodeCost(usd=0.1)) for n in "abcdef"]
    caps += [CapabilitySpec("w.write", "w-op", provides=["w_out"], side_effecting=True, undo="w.undo",
                            compensation={"supported": True, "capability": "w.undo", "verify": "w.verify"}, permissions=["w:write"],
                            cost=NodeCost(usd=0.1)),
             CapabilitySpec("w.read", "w-op", permissions=["w:write"], concurrency_mode="read_only"),
             CapabilitySpec("w.verify", "w-op", permissions=["w:write"]),
             CapabilitySpec("w.undo", "w-op", permissions=["w:write"])]
    reg.register(CapabilityManifest("w-op", caps))
    return reg


def durable_guard(tmp: Path, *, usd=1000.0, actions=1000, grants=("w.*",), tenant="acme", key="env"):
    from agentic_os.mission.authority import (
        AuthorityEnvelope, AuthorityGuard, AutonomyBudget, Grant, SqliteEnvelopeStore, SqliteLedger)
    db = str(tmp / "auth.db")
    SqliteEnvelopeStore(db).put(AuthorityEnvelope(
        tenant=tenant, principal="owner", mission_id=key, issued_at=time.time() - 10, expires_at=time.time() + 100_000,
        grants=tuple(Grant(g) for g in grants), budget=AutonomyBudget(usd=usd, max_actions=actions)))
    return lambda: AuthorityGuard(SqliteEnvelopeStore(db), SqliteLedger(db + ".usage"))


def standing_policy():
    from agentic_os.mission.policy import STANDING, Effect, MissionPolicy, PolicyRule
    return MissionPolicy(id="p", version="1", rules=(PolicyRule("w", Effect.REQUIRE_APPROVAL, "side_effecting", approval_mode=STANDING),))


class CrashingStore:
    """A durable event log that 'kills the process' (raises SimulatedCrash) at the Nth append, before or after the event is persisted."""

    def __new__(cls, path, mode="after"):
        from agentic_os.mission.fork_join import SimulatedCrash
        from agentic_os.mission.store import EventStore

        class _Store(EventStore):
            def __init__(self, p, m):
                super().__init__(p)
                self.mode, self.countdown, self.appended, self.dead = m, None, 0, False

            def arm(self, n):
                self.countdown, self.appended = n, 0

            def append(self, type, mission_id, payload):
                if self.dead:
                    raise SimulatedCrash("dead")
                self.appended += 1
                hit = self.countdown is not None and self.appended == self.countdown
                if hit and self.mode == "before":
                    self.countdown, self.dead = None, True
                    raise SimulatedCrash(f"before {type}")
                ev = super().append(type, mission_id, payload)
                if hit and self.mode == "after":
                    self.countdown, self.dead = None, True
                    raise SimulatedCrash(f"after {type}")
                return ev
        return _Store(path, mode)


def new_process() -> None:
    """The old process is gone: its in-process fork driver claims and locks do not exist in the new one."""
    from agentic_os.mission import fork_join
    fork_join._LIVE_OWNERS.clear()
    fork_join._DRIVER_LOCKS.clear()


class StepsPlanner:
    """The mission's plan is the list of steps after the '|' in its goal ('a,b' independent, 'a>b' chained), so a restart replays exactly the
    plan that ran and a mission with no steps is a plain host for a fork."""

    def plan(self, mission_id, goal, context):
        from agentic_os.mission.types import ExecutionIntent, IntentStep
        spec = goal.split("|", 1)[1] if "|" in goal else ""
        chain = ">" in spec
        names = [x for x in spec.replace(">", ",").split(",") if x]
        return ExecutionIntent(mission_id=mission_id, steps=[
            IntentStep(outcome=f"out_{c}", need=c, inputs_from=[f"out_{names[i - 1]}"] if chain and i else []) for i, c in enumerate(names)])
