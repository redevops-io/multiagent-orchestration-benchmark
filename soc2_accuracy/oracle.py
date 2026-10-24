"""The oracle for the SOC 2 assessment-accuracy lane. It knows only the truth label a fixture was built with and what a safe answer to that truth is. It does not import or
re-implement any assessment procedure, so it cannot agree with Edge Sentinel by sharing its mistakes.

Gates (a single occurrence fails the run) are the errors that would mislead a reviewer or an auditor. Everything else is reported as rates, per fault family and per control, and is
never combined into one number: a lane that averaged "false pass" into "abstained" would hide the one thing that matters."""
from __future__ import annotations

import json
import re

ALLOWED = {
    "COMPLIANT": {"PASS"},
    "NONCOMPLIANT": {"FAIL"},
    "UNKNOWN": {"INCONCLUSIVE", "NOT_TESTED"},
    "MANUAL": {"MANUAL_REVIEW"},
    "NA_DECLARED": {"NOT_APPLICABLE"},
}
GATES = ("false_pass", "unsupported_fail", "unfounded_not_applicable", "forged_evidence_accepted", "cross_tenant_leak", "nondeterministic", "claim_in_export", "export_leak")
INFO = ("missed_detection", "abstained_on_clean", "wrong_rejection_reason")

BANNER = "Readiness evidence for a reviewer. Not an attestation, not an auditor's opinion, and no statement that any criterion is satisfied."
_CLAIM = re.compile(r"\b(?:is|are|was|were|be|been|fully|soc\s?2)\s+(?:compliant|certified|attested)\b|\bcertified\b|compliance score|\bpercent compliant\b|% compliant|\bcompliance\b", re.I)
_CLAIM_KEYS = ("score", "overall", "percent", "compliance", "certified", "compliant", "attested")


def judge(case, result: str, *, rejected: list, evidence_other_tenant: bool, repeat_result: str) -> list:
    """The violations (gate names) and observations (info names) for one case: [(name, detail)]."""
    out: list = []
    truth = case.truth
    if result == "PASS" and truth != "COMPLIANT":
        out.append(("false_pass", f"PASS on {truth.lower()} evidence"))
    if result == "FAIL" and truth in ("COMPLIANT", "UNKNOWN", "MANUAL", "NA_DECLARED"):
        out.append(("unsupported_fail", f"FAIL where the truth is {truth.lower()}"))
    if result == "NOT_APPLICABLE" and truth != "NA_DECLARED":
        out.append(("unfounded_not_applicable", "NOT_APPLICABLE without a declaration"))
    for env, rej in zip(case.envelopes, rejected):
        if env.expect_reject and rej is None:
            out.append(("forged_evidence_accepted", f"the vault accepted evidence that must be refused ({env.expect_reject})"))
        elif env.expect_reject and rej != env.expect_reject:
            out.append(("wrong_rejection_reason", f"refused as {rej!r}, expected {env.expect_reject!r}"))
        elif not env.expect_reject and rej is not None:
            out.append(("wrong_rejection_reason", f"sound evidence was refused ({rej})"))
    if evidence_other_tenant:
        out.append(("cross_tenant_leak", "an assessment cites evidence of another tenant"))
    if repeat_result != result:
        out.append(("nondeterministic", f"{result} then {repeat_result} for the same evidence"))
    if truth == "NONCOMPLIANT" and result in ("INCONCLUSIVE", "NOT_TESTED"):
        out.append(("missed_detection", f"sound evidence of a violation ended {result}"))
    if truth == "COMPLIANT" and result in ("INCONCLUSIVE", "NOT_TESTED", "MANUAL_REVIEW"):
        out.append(("abstained_on_clean", f"sound, compliant evidence ended {result}"))
    return out


def check_export(pack: dict, summary: dict, secrets: tuple) -> list:
    """Independent checks on an exported pack and summary: the banner, no certification or overall claim anywhere, and no raw evidence content."""
    out: list = []
    for name, obj in (("pack", pack), ("summary", summary)):
        if obj.get("banner") != BANNER:
            out.append(("claim_in_export", f"{name}: the banner is missing or changed"))
        body = {k: v for k, v in obj.items() if k != "banner"}
        text = json.dumps(body, sort_keys=True)
        m = _CLAIM.search(text)
        if m:
            out.append(("claim_in_export", f"{name}: claim language {m.group(0)!r}"))
        keys: set = set()

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    keys.add(str(k).lower())
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(body)
        bad = sorted(k for k in keys if any(w in k for w in _CLAIM_KEYS))
        if bad:
            out.append(("claim_in_export", f"{name}: keys that read as a verdict or score: {bad}"))
        for s in secrets:
            if s and s in text:
                out.append(("export_leak", f"{name}: contains raw evidence content or a secret"))
    return out
