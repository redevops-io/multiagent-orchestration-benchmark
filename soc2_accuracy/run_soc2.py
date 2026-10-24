#!/usr/bin/env python3
"""The SOC 2 assessment-accuracy lane: how often does Edge Sentinel's readiness assessment reach a conclusion its evidence does not support?

    python3 soc2_accuracy/run_soc2.py [--edge-sentinel PATH] [--seeds N] [--control naive] [--out FILE]

PATH is an `agentic-os` checkout or its `apps/edge-sentinel` directory (or $EDGE_SENTINEL_PATH). Offline, synthetic fixtures only, no model calls. This lane is scored on its own: it is not
combined with the orchestration, CyberGym or runtime-adversarial results, and it reports no aggregate score. It measures Edge Sentinel's assessments, not any organisation's compliance, and
it makes no claim that a control, criterion or report satisfies SOC 2.

Exit 1 if any gate is violated (see `oracle.GATES`), 2 if the lane could not run."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from soc2_accuracy import fixtures as fx                      # noqa: E402
from soc2_accuracy import oracle                               # noqa: E402


def load_soc2(path: "str | None"):
    """Import the `soc2` package by file path. The app directory is deliberately not put on sys.path: it holds an `operator.py` that would shadow the standard library module."""
    cand = [Path(p) for p in (path, os.environ.get("EDGE_SENTINEL_PATH")) if p]
    cand += [ROOT.parent / "agentic-os"]
    for base in cand:
        for sub in ("soc2", "apps/edge-sentinel/soc2"):
            pkg = base / sub
            if (pkg / "__init__.py").exists():
                spec = importlib.util.spec_from_file_location("soc2", pkg / "__init__.py", submodule_search_locations=[str(pkg)])
                mod = importlib.util.module_from_spec(spec)
                sys.modules["soc2"] = mod
                spec.loader.exec_module(mod)
                return pkg
    raise SystemExit("could not find the soc2 package: pass --edge-sentinel or set EDGE_SENTINEL_PATH")


def revision(pkg: Path) -> str:
    for root in (pkg.parent.parent.parent, pkg.parent):
        head = root / ".git" / "HEAD"
        try:
            ref = head.read_text().strip()
            return (root / ".git" / ref[5:]).read_text().strip()[:12] if ref.startswith("ref: ") else ref[:12]
        except OSError:
            continue
    return "unknown"


import itertools                                                  # noqa: E402
_COUNTER = itertools.count()


class Clock:
    def __call__(self) -> float:
        return fx.NOW


def make_keys(V):
    k = V.ProducerKeys()
    k.register("key-a", "collector-a", ["acme"], fx.SECRET_A)
    k.register("key-b", "collector-b", ["acme", "globex"], fx.SECRET_B)
    return k


def make_envelope(V, C, e):
    if isinstance(e.payload, bytes):                           # a properly signed envelope whose content is not JSON
        env = V.EvidenceEnvelope(tenant=e.tenant, scope=e.scope, kind=e.kind, source="synthetic", producer=e.producer, key_id=e.key_id, payload=e.payload,
                                 observed_at=e.cov[1], coverage_from=e.cov[0], coverage_to=e.cov[1], nonce=e.nonce or os.urandom(8).hex(), classification="restricted", meta={})
        return V.sign_envelope(env, e.secret)
    return C.build_envelope(e.kind, e.payload, tenant=e.tenant, scope=e.scope, source="synthetic", producer=e.producer, key_id=e.key_id, secret=e.secret,
                            observed_at=e.cov[1], coverage_from=e.cov[0], coverage_to=e.cov[1], nonce=e.nonce)


class NaiveAssessor:
    """The negative control: an assessor that believes what it is given. Evidence present means a read of the producer's own verdict; nothing is verified, no coverage or population is
    checked, and the data is not examined. The oracle must catch it."""

    def __init__(self, vault, catalog, clock):
        self.vault, self.clock, self.catalog = vault, clock, {c.control_id: c for c in catalog}

    def declare_not_applicable(self, tenant, scope, control_id, *, by, reason):
        self.na = (tenant, scope, control_id)

    def assess(self, control_id, tenant, scope, period_from, period_to, *, assessor="naive", owner="", supersedes=""):
        from soc2.assess import Assessment
        base = dict(control_id=control_id, control_version="1", tenant=tenant, scope=scope, period_from=period_from, period_to=period_to, assessor=assessor, at=self.clock(), supersedes=supersedes)
        ctl = self.catalog[control_id]
        stored = [s for s in self.vault.find(tenant, scope=scope, since=period_from, until=period_to) if s.kind in ctl.evidence_kinds]
        if not stored:
            return Assessment(**base, result="NOT_TESTED", reason="no evidence")
        ok = True
        for s in stored:
            try:
                p = json.loads(self.vault.read(s.artifact_id, tenant=tenant, actor="naive", purpose="x"))
                ok = ok and bool(p.get("compliant", True)) and not p.get("tampered")
            except Exception:                                    # noqa: BLE001
                ok = True
        return Assessment(**base, result="PASS" if ok else "FAIL", reason="the producer says so", evidence_ids=tuple(s.artifact_id for s in stored))


def run_case(case, S, tmp: Path, naive: bool):
    V, C, A = S["vault"], S["collectors"], S["assess"]
    db = str(tmp / f"case{next(_COUNTER)}.db")
    vault = V.EvidenceVault(db, make_keys(V), clock=Clock())
    rejected: list = []
    receipts = []
    for e in case.envelopes:
        try:
            receipts.append(vault.ingest(make_envelope(V, C, e)))
            rejected.append(None)
        except V.EvidenceRejected as ex:
            rejected.append(getattr(ex, "code", None) or str(ex.args[0] if ex.args else ""))
    if case.tamper:
        case.tamper(db)
    catalog = S["controls"].CATALOG
    assessor = NaiveAssessor(vault, catalog, Clock()) if naive else A.Assessor(vault, clock=Clock())
    if case.na:
        try:
            assessor.declare_not_applicable(case.tenant, case.scope, case.control, by=case.na[0], reason=case.na[1])
        except ValueError:
            pass                                                  # a declaration without a person or a reason is refused: nothing was declared
    a1 = assessor.assess(case.control, case.tenant, case.scope, fx.PERIOD[0], fx.PERIOD[1], owner=case.owner)
    a2 = assessor.assess(case.control, case.tenant, case.scope, fx.PERIOD[0], fx.PERIOD[1], owner=case.owner)
    stored_tenant = {s.artifact_id: s.tenant for s in vault.find("acme", since=0, until=1e18)} | {s.artifact_id: s.tenant for s in vault.find("globex", since=0, until=1e18)}
    leak = any(stored_tenant.get(i, case.tenant) != case.tenant for i in a1.evidence_ids)
    return a1, rejected, leak, a2.result, vault


def export_checks(S, tmp: Path, seed: int) -> list:
    """One vault with a mix of sound and failing evidence, every catalog control assessed and recorded, then the pack and the summary checked by the oracle."""
    V, C, A, L, E = S["vault"], S["collectors"], S["assess"], S["ledger"], S["export"]
    vault = V.EvidenceVault(str(tmp / f"exp{seed}.db"), make_keys(V), clock=Clock())
    ledger = L.Ledger(str(tmp / f"exp{seed}_ledger.db"), clock=Clock())
    cases = [c for c in fx.build_cases(seed) if c.fault in ("none", "producer_claim") and c.envelopes and c.tenant == "acme"]
    seen: set = set()
    for c in cases:                                              # one envelope per control, alternating sound and failing
        if c.control in seen or c.truth not in ("COMPLIANT", "NONCOMPLIANT"):
            continue
        seen.add(c.control)
        for e in c.envelopes:
            try:
                vault.ingest(make_envelope(V, C, e))
            except V.EvidenceRejected:
                pass
    assessor = A.Assessor(vault, clock=Clock())
    for ctl in S["controls"].CATALOG:
        ledger.record_assessment(assessor.assess(ctl.control_id, "acme", "prod", fx.PERIOD[0], fx.PERIOD[1], owner="owner-1"))
    pack = E.restricted_pack(ledger, vault, S["controls"].CATALOG, "acme", "prod", fx.PERIOD[0], fx.PERIOD[1])
    summary = E.benchmark_summary(ledger, "acme", "prod", fx.PERIOD[0], fx.PERIOD[1], S["controls"].CATALOG)
    return oracle.check_export(pack, summary, ("collector-a-synthetic-secret", "collector-b-synthetic-secret"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge-sentinel", default=None)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--control", choices=["naive"], default=None, help="run the oracle against a naive assessor: it must fail")
    ap.add_argument("--out", default=str(ROOT / "results" / "soc2_assessment_accuracy.json"))
    a = ap.parse_args()
    try:
        pkg = load_soc2(a.edge_sentinel)
        import importlib
        S = {n: importlib.import_module(f"soc2.{n}") for n in ("vault", "collectors", "assess", "controls", "ledger", "export")}
    except SystemExit:
        raise
    except Exception:                                            # noqa: BLE001
        traceback.print_exc()
        return 2
    naive = a.control == "naive"
    t0 = time.time()
    totals, by_fault, by_control, firsts = Counter(), defaultdict(Counter), defaultdict(Counter), defaultdict(list)
    cases_run = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for seed in range(a.seeds):
            for case in fx.build_cases(seed):
                try:
                    res, rejected, leak, again, _ = run_case(case, S, tmp, naive)
                except Exception:                                # noqa: BLE001 - a case that cannot run is an error, never a pass
                    traceback.print_exc()
                    return 2
                cases_run += 1
                by_fault[case.fault][res.result] += 1
                by_control[case.control][res.result] += 1
                totals[("truth", case.truth, res.result)] += 1
                for name, detail in oracle.judge(case, res.result, rejected=rejected, evidence_other_tenant=leak, repeat_result=again):
                    totals[name] += 1
                    by_fault[case.fault]["!" + name] += 1
                    if len(firsts[name]) < 5:
                        firsts[name].append(f"{case.id} [{case.fault}]: {detail}")
            if not naive:
                for name, detail in export_checks(S, tmp, seed):
                    totals[name] += 1
                    if len(firsts[name]) < 5:
                        firsts[name].append(f"seed {seed}: {detail}")
    gates = {g: totals[g] for g in oracle.GATES}
    info = {g: totals[g] for g in oracle.INFO}
    confusion = {f"{k[1]}->{k[2]}": n for k, n in sorted((k, n) for k, n in totals.items() if isinstance(k, tuple))}
    report = {
        "lane": "soc2_assessment_accuracy",
        "note": "Measures Edge Sentinel's assessments against synthetic ground truth. Not combined with any other lane; no aggregate score; no claim that any control or criterion is satisfied.",
        "edge_sentinel_revision": revision(pkg), "python": sys.version.split()[0], "seeds": a.seeds, "cases": cases_run, "control": a.control or "none",
        "gates": gates, "gate_violations": sum(gates.values()), "informational": info,
        "truth_to_result": confusion,
        "by_fault": {f: dict(sorted(c.items())) for f, c in sorted(by_fault.items())},
        "by_control": {f: dict(sorted(c.items())) for f, c in sorted(by_control.items())},
        "first_violations": {k: v for k, v in firsts.items() if k in oracle.GATES},
        "first_informational": {k: v for k, v in firsts.items() if k in oracle.INFO},
        "seconds": round(time.time() - t0, 1),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"soc2 lane: {cases_run} cases, {report['gate_violations']} gate violations {gates}")
    print(f"informational (not gates): {info}")
    for k, v in report["first_violations"].items():
        print(f"  {k}: {v[0]}")
    print(f"wrote {a.out}")
    return 1 if report["gate_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
