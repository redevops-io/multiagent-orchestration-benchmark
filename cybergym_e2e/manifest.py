"""The run manifest every lane publishes (one per run): what ran, against which revisions, under which budget, and where the evidence is. A run with a missing or skipped part is `not_tested`."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REQUIRED = ("suite", "run_id", "arm", "source_revisions", "model_ids", "provider", "budget", "seed", "runner_image_digest", "start", "end", "result_uri", "evidence_digests",
            "evaluator_version", "environment", "status", "isolation", "planned_tasks")
STATUSES = ("completed", "partial", "infrastructure_failure", "not_tested")
BUDGET_KEYS = ("agent_timeout_sec", "cost_cap_usd", "max_attempts")
ISOLATION_KEYS = ("firewall", "allowlist", "provider_side_tools_disabled")
EVALUATOR_VERSION = "1"


def sha256_file(path: "str | Path") -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def problems(m: dict) -> list:
    """Why this manifest cannot back a result (an empty list means it can)."""
    out = [f"missing {k}" for k in REQUIRED if k not in m or m[k] in (None, "", [], {})]
    if m.get("status") not in STATUSES:
        out.append(f"status must be one of {STATUSES}")
    if isinstance(m.get("budget"), dict):
        out += [f"budget lacks {k}" for k in BUDGET_KEYS if k not in m["budget"]]
    if isinstance(m.get("isolation"), dict):
        out += [f"isolation lacks {k}" for k in ISOLATION_KEYS if k not in m["isolation"]]
    if isinstance(m.get("source_revisions"), dict):
        out += [f"source_revisions lacks {k}" for k in ("upstream", "dataset") if not m["source_revisions"].get(k)]
    return out
