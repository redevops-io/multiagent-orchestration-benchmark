"""Synthetic fixtures for the SOC 2 assessment-accuracy lane.

Every case is built from a design that already says what is true: how many privileged accounts have no MFA, whether a restore test ran, whether the evidence was altered after it was
stored. The truth label is written by the recipe, never computed by running Edge Sentinel's own procedures, so the oracle is independent of the thing being measured.

Truth labels:
  COMPLIANT     the evidence is sound and the data meets the control's criterion
  NONCOMPLIANT  the evidence is sound and the data shows the criterion was not met
  UNKNOWN       the evidence cannot support a conclusion (altered, partial, wrong target or scope, empty, missing)
  MANUAL        the control needs a person; no automated result may be a PASS
  NA_DECLARED   a named person declared the control not applicable

All data is synthetic: invented tenants, accounts and secrets. No real evidence, no customer data."""
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

NOW = 1_800_000_000.0
DAY = 86_400.0
PERIOD = (NOW - 30 * DAY, NOW)
SECRET_A = b"collector-a-synthetic-secret-0123456789"
SECRET_B = b"collector-b-synthetic-secret-0123456789"
WRONG_SECRET = b"not-the-registered-secret-0123456789ab"

COMPLIANT, NONCOMPLIANT, UNKNOWN, MANUAL, NA_DECLARED = "COMPLIANT", "NONCOMPLIANT", "UNKNOWN", "MANUAL", "NA_DECLARED"
PROCEDURE_CONTROLS = ["SEC-ACC-01", "SEC-ACC-02", "SEC-ACC-03", "SEC-CHG-01", "SEC-MON-01", "SEC-CFG-01", "AVL-BCK-01", "PIN-REC-01"]
MANUAL_CONTROLS = ["SEC-INC-01", "CNF-SEC-01", "PRV-DSR-01"]
KIND = {"SEC-ACC-01": "access_review", "SEC-ACC-02": "access_review", "SEC-ACC-03": "access_review", "SEC-CHG-01": "change_records", "SEC-MON-01": "heartbeat",
        "SEC-CFG-01": "scanner_result", "AVL-BCK-01": "backup_restore", "PIN-REC-01": "reconciliation"}


@dataclass
class Env:
    kind: str
    payload: "dict | bytes"
    tenant: str = "acme"
    scope: str = "prod"
    producer: str = "collector-a"
    key_id: str = "key-a"
    secret: bytes = SECRET_A
    cov: tuple = PERIOD
    nonce: Optional[str] = None
    expect_reject: Optional[str] = None          # the vault must refuse it with this code; accepting it is a violation


@dataclass
class Case:
    id: str
    control: str
    fault: str                                   # the family of fault this case probes ("none" for a clean or plainly failing case)
    truth: str
    envelopes: list = field(default_factory=list)
    tamper: Optional[Callable] = None            # called with the vault db path after ingest: an attacker with disk access
    na: Optional[tuple] = None                   # (by, reason) declared before the assessment
    tenant: str = "acme"
    scope: str = "prod"
    owner: str = ""


# ───────────────────────── payload builders (what a collector would send; the truth is in the numbers chosen) ─────────────────────────
def _acct(i, *, privileged=True, mfa=True, owner="owner-1", reviewed_by="reviewer-1", reviewed_at=None, status="active", terminated_at=None, revoked_at=None):
    return {"id": f"acct-{i}", "privileged": privileged, "mfa": mfa, "owner": owner, "reviewed_by": reviewed_by,
            "reviewed_at": NOW - 5 * DAY if reviewed_at is None else reviewed_at, "status": status, "terminated_at": terminated_at, "access_revoked_at": revoked_at}


def _accounts(rows, complete=True, claim=True):
    return {"accounts": rows, "population_complete": complete, "compliant": claim, "status": "PASS" if claim else "FAIL"}      # the producer's own verdict is noise


def _release(i, *, author="dev-1", approvers=("dev-2",), required=("ci", "scan"), passed=("ci", "scan"), at=None):
    return {"id": f"rel-{i}", "author": author, "approvers": list(approvers), "required_checks": list(required), "passed_checks": list(passed),
            "deployed_at": NOW - 3 * DAY if at is None else at}


def _releases(rows, complete=True):
    return {"releases": rows, "population_complete": complete, "compliant": True}


def _scan(*, target_type="host", critical=0, at=None, target="host-1"):
    return {"target": target, "target_type": target_type, "critical_open": critical, "scanned_at": NOW - 2 * DAY if at is None else at, "compliant": True}


def _backup(*, backups_ok=(True, True, True), restores=((NOW - 10 * DAY, True),), complete=True):
    return {"backups": [{"id": f"bk-{i}", "ok": ok} for i, ok in enumerate(backups_ok)], "restore_tests": [{"at": t, "ok": ok} for t, ok in restores],
            "population_complete": complete, "compliant": True}


def _recon(open_ages_days=(), complete=True):
    return {"outcomes": [{"id": f"o-{i}", "state": "unknown", "resolved_at": None, "opened_at": PERIOD[1] - a * DAY} for i, a in enumerate(open_ages_days)]
            + [{"id": "o-ok", "state": "resolved", "resolved_at": NOW - DAY, "opened_at": NOW - 3 * DAY}], "population_complete": complete, "compliant": True}


def _attest(control, by, expires=None):
    return {"control_id": control, "attested_by": by, "expires_at": NOW + 90 * DAY if expires is None else expires}


# ───────────────────────── recipes: (control, fault, truth, builder(rng)) ─────────────────────────
def build_cases(seed: int) -> list:
    rng = random.Random(seed)
    cases: list = []
    n = lambda lo=3, hi=40: rng.randint(lo, hi)                                    # noqa: E731

    def add(cid, control, fault, truth, envs, **kw):
        cases.append(Case(f"s{seed}:{control}:{cid}", control, fault, truth, envs, **kw))

    def good(control):
        """A clean, compliant evidence payload for a procedure control."""
        if control == "SEC-ACC-01":
            return _accounts([_acct(i, mfa=True) for i in range(n())] + [_acct(900 + i, privileged=False, mfa=False) for i in range(rng.randint(0, 5))])
        if control == "SEC-ACC-02":
            return _accounts([_acct(i, reviewed_by=f"reviewer-{i % 3}") for i in range(n())])
        if control == "SEC-ACC-03":
            return _accounts([_acct(i, status="terminated", terminated_at=NOW - (4 + i) * DAY, revoked_at=NOW - (4 + i) * DAY + 3600) for i in range(rng.randint(1, 8))]
                             + [_acct(500 + i) for i in range(5)])
        if control == "SEC-CHG-01":
            return _releases([_release(i, approvers=(f"dev-{i + 10}",)) for i in range(n())])
        if control == "SEC-MON-01":
            return {"agents": ["a1", "a2"], "heartbeats_per_hour": 60}
        if control == "SEC-CFG-01":
            return _scan()
        if control == "AVL-BCK-01":
            return _backup()
        return _recon([1, 3])                                                       # PIN-REC-01: two unknown outcomes, both younger than the 7-day threshold

    def bad(control):
        """The same shape, with the criterion NOT met in a way the design states (and the producer still claims it is fine)."""
        k = rng.randint(1, 3)
        if control == "SEC-ACC-01":
            rows = [_acct(i, mfa=True) for i in range(n(5, 60))]
            for j in rng.sample(range(len(rows)), k):
                rows[j]["mfa"] = False
            return _accounts(rows)
        if control == "SEC-ACC-02":
            rows = [_acct(i) for i in range(n(5, 40))]
            j = rng.randrange(len(rows))
            kind = rng.choice(["self", "old", "none"])
            rows[j].update({"self": {"reviewed_by": rows[j]["owner"]}, "old": {"reviewed_at": PERIOD[0] - 40 * DAY}, "none": {"reviewed_by": None, "reviewed_at": None}}[kind])
            return _accounts(rows)
        if control == "SEC-ACC-03":
            late = rng.choice([3 * DAY, 20 * DAY, None])
            return _accounts([_acct(1, status="terminated", terminated_at=NOW - 10 * DAY, revoked_at=None if late is None else NOW - 10 * DAY + late)]
                             + [_acct(i, status="terminated", terminated_at=NOW - 12 * DAY, revoked_at=NOW - 12 * DAY + 600) for i in range(2, 6)])
        if control == "SEC-CHG-01":
            rows = [_release(i, approvers=(f"dev-{i + 10}",)) for i in range(n(5, 40))]
            j = rng.randrange(len(rows))
            kind = rng.choice(["self", "nocheck", "noapprover"])
            rows[j].update({"self": {"approvers": [rows[j]["author"]]}, "nocheck": {"passed_checks": ["ci"]}, "noapprover": {"approvers": []}}[kind])
            return _releases(rows)
        if control == "SEC-CFG-01":
            return _scan(critical=rng.randint(1, 5))
        if control == "AVL-BCK-01":
            kind = rng.choice(["norestore", "restorefailed", "backupfailed"])
            if kind == "norestore":
                return _backup(restores=())
            if kind == "restorefailed":
                return _backup(restores=((NOW - 10 * DAY, False), (NOW - 4 * DAY, False)))
            return _backup(backups_ok=(True, False, True))
        return _recon([12, 2])                                                      # PIN-REC-01: one unknown outcome open 12 days

    # ── plain accuracy: sound evidence, the data decides ──
    for c in PROCEDURE_CONTROLS:
        if c == "SEC-MON-01":
            add("clean", c, "none", COMPLIANT, [Env("heartbeat", good(c))])
            continue
        add("clean", c, "none", COMPLIANT, [Env(KIND[c], good(c))])
        add("violation", c, "none", NONCOMPLIANT, [Env(KIND[c], bad(c))])
        add("violation-claimed-compliant", c, "producer_claim", NONCOMPLIANT, [Env(KIND[c], {**bad(c), "compliant": True, "status": "PASS", "attested_compliant": True})])
    # vacuous populations are only a pass where the control allows an empty population AND the producer says the population is complete
    add("no-terminations-complete", "SEC-ACC-03", "none", COMPLIANT, [Env("access_review", _accounts([_acct(i) for i in range(5)]))])
    add("no-releases-complete", "SEC-CHG-01", "none", COMPLIANT, [Env("change_records", _releases([]))])
    add("no-open-outcomes-complete", "PIN-REC-01", "none", COMPLIANT, [Env("reconciliation", {"outcomes": [], "population_complete": True})])
    add("terminated-before-period-ignored", "SEC-ACC-03", "none", COMPLIANT,
        [Env("access_review", _accounts([_acct(1, status="terminated", terminated_at=PERIOD[0] - 60 * DAY, revoked_at=None)] + [_acct(i) for i in range(2, 6)]))])
    add("release-before-period-ignored", "SEC-CHG-01", "none", COMPLIANT,
        [Env("change_records", _releases([_release(1, author="d", approvers=[], at=PERIOD[0] - 90 * DAY)] + [_release(i, approvers=(f"x{i}",)) for i in range(2, 8)]))])
    add("unknown-outcome-under-threshold", "PIN-REC-01", "none", COMPLIANT, [Env("reconciliation", _recon([6]))])
    add("nonprivileged-without-mfa-is-fine", "SEC-ACC-01", "none", COMPLIANT,
        [Env("access_review", _accounts([_acct(1), _acct(2)] + [_acct(100 + i, privileged=False, mfa=False) for i in range(10)]))])
    add("hidden-violation-in-large-population", "SEC-ACC-01", "none", NONCOMPLIANT,
        [Env("access_review", _accounts([_acct(i) for i in range(400)] + [_acct(9999, mfa=False)]))])

    # ── evidence that cannot support a conclusion, though the data it carries looks compliant ──
    for c in PROCEDURE_CONTROLS:
        k, payload = KIND[c], good(c)
        add("gap-60pct", c, "coverage_gap", UNKNOWN, [Env(k, payload, cov=(PERIOD[0], PERIOD[0] + 0.6 * (PERIOD[1] - PERIOD[0])))])
        add("stale-before-period", c, "stale", UNKNOWN, [Env(k, payload, cov=(PERIOD[0] - 90 * DAY, PERIOD[0] - 60 * DAY))])
        add("other-scope", c, "wrong_scope", UNKNOWN, [Env(k, payload, scope="staging")])
        add("other-tenant-only", c, "wrong_tenant", UNKNOWN, [Env(k, payload, tenant="globex", producer="collector-b", key_id="key-b", secret=SECRET_B)])
        add("forged-signature", c, "forged", UNKNOWN, [Env(k, payload, secret=WRONG_SECRET, expect_reject="bad_signature")])
        add("unknown-key", c, "forged", UNKNOWN, [Env(k, payload, key_id="key-zzz", expect_reject="unknown_key")])
        add("producer-not-allowed-for-tenant", c, "forged", UNKNOWN, [Env(k, payload, tenant="initech", expect_reject="tenant_not_allowed")])
        add("future-coverage", c, "forged", UNKNOWN, [Env(k, payload, cov=(NOW - DAY, NOW + 30 * DAY), expect_reject="future_coverage")])
        add("unreadable-payload", c, "malformed", UNKNOWN, [Env(k, b"\x00\xffnot json at all")])
        add("tampered-content", c, "tamper", UNKNOWN, [Env(k, payload)], tamper=_tamper_content)
        add("tampered-header-scope", c, "tamper", UNKNOWN, [Env(k, payload)], tamper=_tamper_header)
        add("sound-plus-one-tampered", c, "tamper", UNKNOWN, [Env(k, payload, cov=(PERIOD[0], NOW - 10 * DAY)), Env(k, payload, cov=(NOW - 10 * DAY, NOW))], tamper=_tamper_content_last)
        if c not in ("SEC-MON-01",):
            add("population-not-complete", c, "incomplete", UNKNOWN, [Env(k, _incomplete(payload))])
        add("no-evidence", c, "missing", UNKNOWN, [])
    for c in ("SEC-ACC-01", "SEC-ACC-02"):                      # an empty list of accounts is what a collector that failed also sends: not a pass, not a failure
        add("empty-population-claimed-fine", c, "empty", UNKNOWN, [Env(KIND[c], _accounts([]))])
    # a COMPLETE population of no backups and no restore tests is not an unreadable one: it shows that nothing was restore-tested in the period
    add("no-backups-no-restore-tests-complete", "AVL-BCK-01", "none", NONCOMPLIANT, [Env("backup_restore", {"backups": [], "restore_tests": [], "population_complete": True})])
    add("wrong-scan-target-type", "SEC-CFG-01", "wrong_target", UNKNOWN, [Env("scanner_result", _scan(target_type="container", critical=0))])
    add("scan-outside-period", "SEC-CFG-01", "stale", UNKNOWN, [Env("scanner_result", _scan(at=PERIOD[0] - 20 * DAY))])
    add("replayed-envelope-does-not-double-count", "SEC-ACC-01", "replay", COMPLIANT,
        [Env("access_review", good("SEC-ACC-01"), nonce="fixed-nonce-1"), Env("access_review", good("SEC-ACC-01"), nonce="fixed-nonce-1")])

    # ── controls that need a person ──
    for c in MANUAL_CONTROLS:
        add("no-attestation", c, "none", MANUAL, [], owner="owner-1")
        add("attested-by-an-independent-person", c, "none", MANUAL, [Env("attestation", _attest(c, "auditor-liaison"))], owner="owner-1")
        add("self-attested-by-the-owner", c, "self_attest", MANUAL, [Env("attestation", _attest(c, "owner-1"))], owner="owner-1")
        add("attestation-expired", c, "expired", MANUAL, [Env("attestation", _attest(c, "auditor-liaison", expires=NOW - DAY))], owner="owner-1")
        add("attestation-for-another-control", c, "wrong_control", MANUAL, [Env("attestation", _attest("SEC-ACC-01", "auditor-liaison"))], owner="owner-1")

    # ── applicability is declared by a person, never inferred ──
    add("declared-by-a-named-person", "SEC-CFG-01", "none", NA_DECLARED, [], na=("j.doe", "no hosts are in scope for this tenant: managed service only"))
    add("absent-evidence-is-not-not-applicable", "SEC-CFG-01", "inferred_na", UNKNOWN, [])
    add("declaration-without-a-reason-is-refused", "AVL-BCK-01", "inferred_na", UNKNOWN, [], na=("j.doe", "n/a"))
    add("declaration-without-a-person-is-refused", "AVL-BCK-01", "inferred_na", UNKNOWN, [], na=("  ", "no backups are taken for this tenant at all, by design"))
    return cases


def _incomplete(payload: dict) -> dict:
    return {**payload, "population_complete": False}


# ───────────────────────── attacker with access to the vault file ─────────────────────────
def _tamper_content(db: str) -> None:
    _rewrite(db, "UPDATE evidence SET content = ? WHERE seq = (SELECT MIN(seq) FROM evidence)", (json.dumps({"accounts": [], "population_complete": True, "compliant": True}).encode(),))


def _tamper_content_last(db: str) -> None:
    _rewrite(db, "UPDATE evidence SET content = ? WHERE seq = (SELECT MAX(seq) FROM evidence)", (b'{"tampered": true, "population_complete": true}',))


def _tamper_header(db: str) -> None:
    _rewrite(db, "UPDATE evidence SET coverage_from = coverage_from - 1000000, coverage_to = coverage_to + 1000000, scope = scope WHERE seq = (SELECT MIN(seq) FROM evidence)", ())


def _rewrite(db: str, sql: str, args: tuple) -> None:
    """The append-only triggers stop the application; a person with the file can drop them. The vault must still notice the result."""
    c = sqlite3.connect(db, isolation_level=None)
    try:
        trig = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='evidence'")]
        for t in trig:
            c.execute(f"DROP TRIGGER {t}")
        c.execute(sql, args)
    finally:
        c.close()
