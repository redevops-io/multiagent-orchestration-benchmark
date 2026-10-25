"""Contract tests for the runtime-adversarial lane: coverage statuses are computed from references that resolve, a skipped or failing scenario never counts as covered, the manifest is complete, and the
runner's exit codes mean what they say. No agentic-os checkout is needed: the checkout is a small synthetic tree."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from governed import scenarios as S                   # noqa: E402
from runtime_adversarial import run_lane as RL        # noqa: E402

COVERAGE = json.loads((ROOT / "runtime_adversarial" / "coverage.json").read_text())
CONTROLS = json.loads((ROOT / "runtime_adversarial" / "controls.json").read_text())
PINS = json.loads((ROOT / "runtime_adversarial" / "PINS.json").read_text())
KEYS = ("positive_control", "negative_control_in_ci", "defect_seeded_revision_in_ci", "false_denial_rate_measured", "trace_in_results", "policy_decision_in_results", "effects_counted_independently")


def tree(tmp_path, files: dict):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return str(tmp_path)


def cov(*items):
    return {"items": [{"id": f"X{i}", "family": "authority", "item": "t", "governed": g, "source_tests": t, "note": ""} for i, (g, t) in enumerate(items)]}


def res(**kw):
    return {name: {"seeds": 1, "checks": 5, "violations": 0, **r} for name, r in kw.items()}


# ───────────────────────── the committed data ─────────────────────────
def test_every_item_is_well_formed_and_ids_are_unique():
    ids = [i["id"] for i in COVERAGE["items"]]
    assert len(ids) == len(set(ids)) == 41
    assert all(i["family"] in RL.FAMILIES and i["item"] and isinstance(i["governed"], list) and isinstance(i["source_tests"], list) for i in COVERAGE["items"])
    assert {i["family"] for i in COVERAGE["items"]} == set(RL.FAMILIES)


def test_every_governed_scenario_a_mapping_names_is_registered():
    reg = set(S.SCENARIOS)
    for i in COVERAGE["items"]:
        assert set(i["governed"]) <= reg, i["id"]


def test_every_registered_scenario_declares_its_controls_and_only_instrumented_ones_claim_a_false_denial_rate():
    assert set(CONTROLS["scenarios"]) == set(S.SCENARIOS)
    measured = {n for n, c in CONTROLS["scenarios"].items() if c["false_denial_rate_measured"]}
    assert measured == {"child_agent_tree", "projects_routes", "agent_in_the_loop", "provider_reconciliation", "cross_model_delegation", "retrieval_isolation", "rag_indexer_identity"}
    for name, c in CONTROLS["scenarios"].items():
        assert set(c) - {"legitimacy"} == set(KEYS), name
        assert bool(c.get("legitimacy")) == c["false_denial_rate_measured"], f"{name}: a scenario that measures false denials must say what 'legitimate' means, and one that does not must not"


def test_the_pins_are_full_commit_hashes_for_all_sources():
    for k in ("agentic-os", "agent-harness", "context-runtime", "redevops-rag"):
        assert len(PINS["sources"][k]["revision"]) == 40 and all(c in "0123456789abcdef" for c in PINS["sources"][k]["revision"])


def test_items_with_nothing_mapped_say_why_in_a_note():
    for i in COVERAGE["items"]:
        if not i["governed"] and not i["source_tests"]:
            assert i["note"], i["id"]


# ───────────────────────── coverage statuses ─────────────────────────
def test_a_passing_governed_scenario_beats_a_named_test_and_a_named_test_beats_nothing(tmp_path):
    co = tree(tmp_path, {"tests/test_a.py": "def test_the_thing_holds():\n    pass\n"})
    c = RL.evaluate_coverage(cov((["projects_routes"], []), ([], [{"file": "tests/test_a.py", "name": "the_thing_holds"}]), ([], [])), res(projects_routes={}), set(S.SCENARIOS), co)
    assert [i["status"] for i in c["items"]] == ["has_governed_scenario", "has_named_test", "not_tested"]
    assert c["by_family"]["authority"] == {"items": 3, "has_governed_scenario": 1, "has_infra_check": 0, "has_named_test": 1, "not_tested": 1, "failing": 0}


def test_a_failing_scenario_makes_the_item_failing_not_covered():
    c = RL.evaluate_coverage(cov((["projects_routes", "child_agent_tree"], [])), res(projects_routes={"violations": 2}, child_agent_tree={}), set(S.SCENARIOS), None)
    assert c["items"][0]["status"] == "failing" and c["items"][0]["governed_failing"] == ["projects_routes"]


@pytest.mark.parametrize("r", [{"skipped": "fastapi is not installed"}, {"error": "seed 0: boom"}])
def test_a_skipped_or_errored_scenario_is_not_tested_never_a_pass(r):
    c = RL.evaluate_coverage(cov((["projects_routes"], [])), res(projects_routes=r), set(S.SCENARIOS), None)
    assert c["items"][0]["status"] == "not_tested" and c["items"][0]["governed_not_run"] == ["projects_routes"]


def test_a_scenario_with_no_result_at_all_is_not_tested():
    assert RL.evaluate_coverage(cov((["projects_routes"], [])), {}, set(S.SCENARIOS), None)["items"][0]["status"] == "not_tested"


def test_a_skipped_scenario_falls_back_to_a_named_test_when_there_is_one(tmp_path):
    co = tree(tmp_path, {"tests/test_a.py": "def test_x_y():\n    pass\n"})
    c = RL.evaluate_coverage(cov((["projects_routes"], [{"file": "tests/test_a.py", "name": "x_y"}])), res(projects_routes={"skipped": "no fastapi"}), set(S.SCENARIOS), co)
    assert c["items"][0]["status"] == "has_named_test"


@pytest.mark.parametrize("files,ref", [({}, {"file": "tests/test_a.py", "name": "x"}),
                                       ({"tests/test_a.py": "def test_other():\n    pass\n"}, {"file": "tests/test_a.py", "name": "x"}),
                                       ({"tests/test_a.py": "# test_x is only mentioned in a comment\n"}, {"file": "tests/test_a.py", "name": "x"})])
def test_a_reference_that_does_not_resolve_is_reported_and_never_counts(tmp_path, files, ref):
    c = RL.evaluate_coverage(cov(([], [ref])), {}, set(S.SCENARIOS), tree(tmp_path, files))
    assert c["items"][0]["status"] == "not_tested" and any("not found in the checkout" in p for p in c["reference_problems"])


def test_a_mapping_to_an_unregistered_scenario_is_a_problem():
    c = RL.evaluate_coverage(cov((["no_such_scenario"], [])), res(), set(S.SCENARIOS), None)
    assert c["items"][0]["status"] == "not_tested" and any("not registered" in p for p in c["reference_problems"])


def test_without_a_checkout_no_named_test_counts():
    c = RL.evaluate_coverage(cov(([], [{"file": "tests/test_a.py", "name": "x"}])), {}, set(S.SCENARIOS), None)
    assert c["items"][0]["status"] == "not_tested"


def test_the_coverage_summary_has_counts_per_family_and_no_single_score():
    c = RL.evaluate_coverage(COVERAGE, res(), set(S.SCENARIOS), None)
    text = json.dumps(c).lower()
    assert set(c["by_family"]) == set(RL.FAMILIES) and "score" not in text.replace("no score is computed", "") and "overall" not in text


# ───────────────────────── manifest ─────────────────────────
def manifest(rc=0, results=None, **kw):
    results = results if results is not None else {"scenarios": {"a": {"violations": 0}}, "profile": "production"}
    return RL.build_manifest(PINS, {"agentic-os": "x"}, 12, rc, "s", "e", "abc", "file:r.json", results, kw.get("require_all", True))


def test_a_clean_run_has_a_complete_manifest_with_no_not_tested():
    m = manifest()
    assert RL.manifest_problems(m) == [] and m["status"] == "completed" and m["not_tested"] == {"skipped_scenarios": [], "errored_scenarios": []}
    assert m["model_ids"] and m["budget"]["model_cost_usd"] == 0 and m["evidence_digests"] == {"governed_properties.json": "abc"}


def test_a_skipped_or_errored_scenario_makes_the_run_partial_and_is_named():
    m = manifest(results={"scenarios": {"a": {"skipped": "x"}, "b": {"error": "boom"}, "c": {}}})
    assert m["status"] == "partial" and m["not_tested"] == {"skipped_scenarios": ["a"], "errored_scenarios": ["b"]}


def test_a_violation_is_a_partial_run_and_a_runner_failure_is_an_infrastructure_failure():
    assert manifest(rc=1)["status"] == "partial"
    assert manifest(rc=2)["status"] == "infrastructure_failure"


def test_manifest_problems_name_what_is_missing():
    m = manifest()
    del m["seed"]
    m["result_uri"] = ""
    assert set(RL.manifest_problems(m)) == {"missing seed", "missing result_uri"}


# ───────────────────────── the runner's exit codes ─────────────────────────
INFRA_OK = {n: {"id": n, "status": "pass"} for n in ("network_isolation", "image_pinning", "log_chain")}


def run_cli(tmp_path, governed: dict, *flags, agentic_os=None, infra=None):
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps(governed))
    ip = tmp_path / "i.json"
    ip.write_text(json.dumps(INFRA_OK if infra is None else infra))
    flags = ("--infra-results", str(ip), *flags)
    out = tmp_path / "out.json"
    r = subprocess.run([sys.executable, str(ROOT / "runtime_adversarial" / "run_lane.py"), "--agentic-os", agentic_os or str(tmp_path), "--governed-results", str(gp), "--out", str(out), *flags],
                       capture_output=True, text=True)
    return r.returncode, (json.loads(out.read_text()) if out.exists() else None), r.stdout


GOOD = {"profile": "production", "scenarios": {n: {"seeds": 1, "checks": 3, "violations": 0} for n in S.SCENARIOS}}


def test_a_checkout_that_is_not_the_pinned_revision_stops_the_run(tmp_path):
    code, rep, out = run_cli(tmp_path, GOOD)
    assert code == 3 and rep is None and "not the pinned revision" in out


def test_with_unpinned_allowed_a_clean_run_exits_zero_and_the_report_says_it_was_unpinned(tmp_path):
    code, rep, _ = run_cli(tmp_path, GOOD, "--allow-unpinned")
    assert code == 0 and rep["manifest"]["pins_match"] is False and "agentic-os" in rep["manifest"]["unpinned"]


def test_a_violation_exits_one(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    bad["scenarios"]["projects_routes"]["violations"] = 1
    code, rep, _ = run_cli(tmp_path, bad, "--allow-unpinned")
    assert code == 1 and any(i["status"] == "failing" for i in rep["coverage"]["items"])


def test_a_skipped_scenario_is_tolerated_by_default_and_fails_the_run_when_all_are_required(tmp_path):
    sk = json.loads(json.dumps(GOOD))
    sk["scenarios"]["projects_routes"] = {"seeds": 1, "checks": 0, "violations": 0, "skipped": "fastapi is not installed"}
    c1, rep, _ = run_cli(tmp_path, sk, "--allow-unpinned")
    c2, _, _ = run_cli(tmp_path, sk, "--allow-unpinned", "--require-all")
    assert c1 == 0 and rep["manifest"]["status"] == "partial" and c2 == 2


def test_an_errored_scenario_exits_two(tmp_path):
    er = json.loads(json.dumps(GOOD))
    er["scenarios"]["goal_loop_chaos"] = {"seeds": 1, "checks": 0, "violations": 0, "error": "seed 0: boom"}
    assert run_cli(tmp_path, er, "--allow-unpinned")[0] == 2


def test_the_report_states_what_is_not_measured_and_not_built(tmp_path):
    _, rep, _ = run_cli(tmp_path, GOOD, "--allow-unpinned")
    assert rep["not_built"] and "no score is combined" in rep["no_aggregate"]
    assert all(v == "not measured" for v in rep["false_denials"]["by_scenario"].values())          # GOOD has no counters: not measured, never zero


# ───────────────────────── false denials ─────────────────────────
def test_the_result_counts_legitimate_actions_and_keeps_the_refused_ones_with_their_reasons():
    r = S.Result()
    assert r.legit(True, "ok") is True and r.legit(False, "refused: reason x") is False
    assert r.legit_total == 2 and r.legit_refused == ["refused: reason x"]


def test_a_false_denial_rate_is_reported_where_counted_and_not_measured_where_not_never_zero():
    scen = {"a": {"legitimate_actions": 40, "legitimate_refused": 1, "first_false_denials": ["seed 3: x"]}, "b": {"legitimate_actions": 0, "legitimate_refused": 0},
            "c": {}, "d": {"legitimate_actions": 10, "legitimate_refused": 0, "skipped": "no fastapi"}, "e": {"legitimate_actions": 10, "legitimate_refused": 0, "error": "boom"}}
    out = RL.false_denials(scen)
    assert out["a"] == {"legitimate_actions": 40, "refused": 1, "rate": 0.025, "first_refusals": ["seed 3: x"]}
    assert out["b"] == out["c"] == out["d"] == out["e"] == "not measured"


def test_the_cli_report_carries_the_rates_from_the_suite_results(tmp_path):
    g = json.loads(json.dumps(GOOD))
    g["scenarios"]["child_agent_tree"].update({"legitimate_actions": 100, "legitimate_refused": 4, "first_false_denials": ["seed 1: step 2"]})
    _, rep, _ = run_cli(tmp_path, g, "--allow-unpinned")
    fd = rep["false_denials"]["by_scenario"]
    assert fd["child_agent_tree"]["rate"] == 0.04 and fd["goal_loop_chaos"] == "not measured"


# ───────────────────────── infrastructure checks ─────────────────────────
def infra_cov(*names):
    return {"items": [{"id": "X0", "family": "infrastructure", "item": "t", "governed": [], "source_tests": [], "infra": list(names), "note": ""}]}


def test_an_infra_check_that_passed_covers_its_item_and_one_that_did_not_run_never_does():
    ok = RL.evaluate_coverage(infra_cov("a"), {}, set(), None, {"a": {"status": "pass"}})
    assert ok["items"][0]["status"] == "has_infra_check"
    for st in ("not_tested", "error"):
        c = RL.evaluate_coverage(infra_cov("a"), {}, set(), None, {"a": {"status": st}})
        assert c["items"][0]["status"] == "not_tested" and c["items"][0]["infra_not_run"] == ["a"]
    assert RL.evaluate_coverage(infra_cov("a"), {}, set(), None, None)["items"][0]["status"] == "not_tested"
    assert RL.evaluate_coverage(infra_cov("a"), {}, set(), None, {"a": {"status": "fail"}})["items"][0]["status"] == "failing"


def test_an_item_with_two_checks_is_covered_only_when_both_ran_and_passed():
    c = RL.evaluate_coverage(infra_cov("a", "b"), {}, set(), None, {"a": {"status": "pass"}, "b": {"status": "not_tested"}})
    assert c["items"][0]["status"] == "not_tested"


def test_the_four_formerly_untested_categories_are_now_mapped_to_something_that_runs():
    items = {i["id"]: i for i in COVERAGE["items"]}
    assert items["A07"]["governed"] == ["cross_model_delegation"]
    assert set(items["C04"]["governed"]) == {"retrieval_isolation", "rag_indexer_identity"}
    assert items["I02"]["infra"] == ["network_isolation"] and items["I04"]["infra"] == ["image_pinning"] and items["I05"]["infra"] == ["log_chain"]


def test_a_failing_infra_check_exits_one_and_an_errored_one_exits_two(tmp_path):
    bad = {**INFRA_OK, "image_pinning": {"id": "image_pinning", "status": "fail"}}
    code, rep, _ = run_cli(tmp_path, GOOD, "--allow-unpinned", infra=bad)
    assert code == 1 and rep["coverage"]["items"] and any(i["status"] == "failing" for i in rep["coverage"]["items"])
    err = {**INFRA_OK, "log_chain": {"id": "log_chain", "status": "error", "reason": "boom"}}
    assert run_cli(tmp_path, GOOD, "--allow-unpinned", infra=err)[0] == 2


def test_an_infra_check_that_could_not_run_makes_the_run_partial_and_fails_require_all(tmp_path):
    nt = {**INFRA_OK, "network_isolation": {"id": "network_isolation", "status": "not_tested", "reason": "no docker"}}
    c1, rep, _ = run_cli(tmp_path, GOOD, "--allow-unpinned", infra=nt)
    c2, _, _ = run_cli(tmp_path, GOOD, "--allow-unpinned", "--require-all", infra=nt)
    assert c1 == 0 and rep["manifest"]["status"] == "partial" and rep["manifest"]["not_tested"]["infra_checks"] == ["network_isolation"] and c2 == 2


# the checks themselves, on small synthetic trees (the docker one needs a daemon and runs in the lane)
sys.path.insert(0, str(ROOT / "runtime_adversarial"))
import infra as I                                         # noqa: E402


def test_the_image_classifier_agrees_with_its_truth_table():
    for ref, want in I.FIXTURES:
        assert I.classify_ref(ref) == want, ref


def test_the_image_scan_reads_dockerfiles_and_yaml_and_skips_stage_names_and_comments(tmp_path):
    tree(tmp_path, {"Dockerfile": I.DOCKERFILE_FIXTURE, "deploy/c.yml": I.YAML_FIXTURE, "notes.md": "image: ignored:1"})
    got = {(f, r, c) for f, r, c in I.scan_images(tmp_path)}
    assert ("Dockerfile", "python:3.12", "mutable") in got and ("Dockerfile", "python@sha256:" + "b" * 64, "immutable") in got
    assert ("deploy/c.yml", "y:1", "mutable") in got and not any(f == "notes.md" for f, _, _ in got)
    assert not any(r in ("scratch", "build") for _, r, _ in got)


def test_a_new_mutable_reference_outside_the_baseline_fails_the_check(tmp_path):
    tree(tmp_path, {"Dockerfile": "FROM python:9.9\n"})
    r = I.check_image_pinning(str(tmp_path))
    assert r.status == "fail" and any("python:9.9" in c["detail"] for c in r.checks if not c["ok"])


def test_the_log_chain_check_passes_on_the_real_store_and_fails_when_the_verifier_is_blind(monkeypatch):
    import os
    ao = os.environ.get("AGENTIC_OS_CHECKOUT")
    if not ao:
        pytest.skip("set AGENTIC_OS_CHECKOUT to a checkout of agentic-os to run the log-chain check here (the lane runs it against the pinned revision)")
    assert I.check_log_chain(ao).status == "pass"
    monkeypatch.setenv("RA_LOG_BUG", "no_verify")
    assert I.check_log_chain(ao).status == "fail"


def test_k8s_static_check_accepts_a_default_deny_and_flags_an_open_rule_on_any_pod(tmp_path):
    good = ("kind: NetworkPolicy\nmetadata: {name: a}\nspec:\n  podSelector: {}\n  policyTypes: [Egress]\n---\nkind: NetworkPolicy\nmetadata: {name: b}\nspec:\n  podSelector:\n    matchLabels: {app: egress-proxy}\n  egress:\n"
            "    - to:\n        - ipBlock:\n            cidr: 0.0.0.0/0\n            except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10]\n---\nkind: NetworkPolicy\nmetadata: {name: c}\nspec:\n  podSelector: {}\n")
    p = tmp_path / "p.yaml"
    p.write_text(good)
    assert all(ok for _, ok, _ in I._k8s_static(p))
    p.write_text(good.replace("matchLabels: {app: egress-proxy}", "matchLabels: {app: web}"))
    assert not all(ok for _, ok, _ in I._k8s_static(p))
    p.write_text(good.replace("169.254.0.0/16, ", ""))
    assert not all(ok for _, ok, _ in I._k8s_static(p))
