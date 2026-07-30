#!/usr/bin/env python3
"""Validate the deterministic gates against the reference solutions — no model calls, no API keys.
Run:  python3 validate_gates.py    (requires kotlinc + java on PATH; see README)"""
import sys, pathlib, tempfile
sys.path.insert(0, "harness")
import bench
REF = {"shared_contract":"reference/shared_contract_reference.kt",
       "independent_modules":"reference/independent_modules_reference.kt",
       "cross_cutting":"reference/cross_cutting_reference.kt"}
ok = True
for shape, ref in REF.items():
    run = pathlib.Path(tempfile.mkdtemp())/"v"; run.mkdir(parents=True)
    r = bench.gates(open(ref).read(), run, "ref", shape)
    print(f"{shape:20} build={r['build']} tests={r['tests_pass']}/{r['tests_total']} arch={r['arch_ok']} PASS={r['gate_pass']}")
    ok = ok and r["gate_pass"]
print("\nALL REFERENCE GATES PASS" if ok else "\nSOME GATE FAILED"); sys.exit(0 if ok else 1)
