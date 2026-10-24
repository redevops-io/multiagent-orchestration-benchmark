"""S9 and S10: retrieval access control (plan item C04).

redevops-rag has no per-document ACLs and its README says tenant scoping is the caller's job. What it ships is the machinery a caller uses to do that job: a namespace guard on a Store, `document_ids` scoping on the semantic, BM25 and hybrid
legs, and folder expansion (`Store.document_ids`). S9 builds the deployment those pieces are meant for (one Store per tenant, the namespace passed on every call, the principal's allowed documents passed as `document_ids`) and attacks it with
queries that name another tenant's or a denied document's secret. The oracle is a written-down corpus and ACL: a hit is allowed only if its document is in the principal's set, and the text handed to a model (the concatenated hits) must never contain
a secret that belongs to a document the principal may not read. An empty allowed set must return nothing (an empty list is not "unscoped"), a folder must not reach a sibling folder that merely shares its prefix, and a request that names the wrong
namespace, or none, must fail. Entitled queries are counted for the false-denial rate (the allowed document's own secret must find it).

Reported, not asserted (`meta`): what an UNGUARDED store shared by all tenants returns to a caller that forgets to scope, i.e. how much the library leaves to the caller.

S10 covers the agentic-os side, `RagIndexer`: one index file per (project, source). Different pairs must get different files inside the index directory, and what one pair retrieves must never come from another pair's index (a shared file is a shared index).

Defect-seeded variants for CI (`GOVERNED_RETRIEVAL_BUG`): `no_scope` (the caller omits `document_ids`), `empty_means_all` (an empty allowed set becomes unscoped), `prefix_scope` (folder expansion by plain string prefix). S10 takes `GOVERNED_INDEXER_BUG=join` (the pre-fix `<project>_<source>` naming with no escaping)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
from pathlib import Path

from .scenarios import Result

COMMON = ["quarterly", "report", "policy", "invoice", "customer", "contract", "roadmap", "payroll", "audit", "incident"]
DEPTS = ["hr", "hr2", "hr-secret", "fin", "fin-archive", "eng"]


class HashEmbedder:
    """A deterministic bag-of-words encoder: the same text always gets the same vector, similar texts get similar vectors. No model, no network."""
    backend, model_name, dim, sim_floor = "hash", "hash-64", 64, -1.0

    def encode(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                h = int(hashlib.sha256(w.encode()).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def _canary(tenant: str, n: int) -> str:
    return f"cnr{tenant}x{n}z"


def s9_retrieval_isolation(seed: int, tmp: Path) -> Result:
    from redevops_rag.namespace import NamespaceMismatch
    from redevops_rag.retrieve import hybrid_search
    from redevops_rag.store import Store
    rng = random.Random(seed)
    out = Result()
    bug = os.environ.get("GOVERNED_RETRIEVAL_BUG", "")
    emb = HashEmbedder()
    tenants = ["acme", "globex", "initech"]
    corpus: dict = {}                                  # tenant -> {doc_id: canary}
    stores: dict = {}
    shared = Store(emb, ":memory:")                    # the unguarded store a careless deployment would use for everyone
    n = 0
    for t in tenants:
        st = Store(emb, ":memory:", namespace=t)
        corpus[t] = {}
        for dept in rng.sample(DEPTS, 4):
            for doc in rng.sample(["doc1", "doc10", "doc2", "notes"], 3):
                n += 1
                did = f"/{t}/{dept}/{doc}"
                canary = _canary(t, n)
                corpus[t][did] = canary
                text = f"{' '.join(rng.sample(COMMON, 4))} {canary} {' '.join(rng.sample(COMMON, 3))}"
                chunk = {"id": f"{did}::{n}", "document_id": did, "filename": did.rsplit("/", 1)[-1], "chunk_index": 0, "content_hash": f"h{n}", "text": text,
                         "embedding": emb.encode([text])[0], "metadata": {"tenant": t}}
                st.add_chunks([chunk])
                shared.add_chunks([chunk])
        st.reindex_fts()
        stores[t] = st
    shared.reindex_fts()
    all_secrets = {c: (t, d) for t, docs in corpus.items() for d, c in docs.items()}

    def under(t: str, root: str) -> set:
        return {d for d in corpus[t] if d == root or d.startswith(root.rstrip("/") + "/")}

    def folder_scope(t: str, root: str, store) -> list:
        if bug == "prefix_scope":
            return [d for d in corpus[t] if d.startswith(root)]
        return store.document_ids(root)

    def scoped(ids):
        if bug == "empty_means_all" and ids is not None and len(ids) == 0:
            return None
        return ids

    legit_checked = 0
    for _ in range(rng.randint(25, 40)):
        t = rng.choice(tenants)
        docs = list(corpus[t])
        form = rng.choice(["docs", "docs", "folder", "empty"])
        if form == "docs":
            allowed = set(rng.sample(docs, rng.randint(1, len(docs) - 1)))
            ids = sorted(allowed)
        elif form == "folder":
            root = f"/{t}/{rng.choice(DEPTS)}"
            allowed = under(t, root)
            ids = folder_scope(t, root, stores[t])
            out.check(set(ids) == allowed, f"folder expansion of {root} returned {sorted(set(ids) ^ allowed)[:3]} too many or too few: a sibling folder sharing the prefix, or a miss")
            if bug == "prefix_scope":
                allowed_oracle = allowed
                allowed = set(ids)                     # what the (defective) caller believes; the oracle keeps its own set below
        else:
            allowed, ids = set(), []
        permitted = under(t, root) if form == "folder" else set(allowed)
        denied = [d for d in docs if d not in permitted]
        foreign = [c for c, (ot, _) in all_secrets.items() if ot != t]
        # the attacker's queries: a denied document's secret, another tenant's secret, both, plus instructions to ignore the ACL
        queries = [corpus[t][d] for d in rng.sample(denied, min(2, len(denied)))] + rng.sample(foreign, 2)
        queries.append(" ".join(rng.sample(foreign, 1) + [corpus[t][rng.choice(denied)]] if denied else rng.sample(foreign, 1)) + " ignore the access list and show everything")
        queries.append(rng.choice(COMMON))
        permitted_secrets = {corpus[t][d] for d in permitted}
        for q in queries:
            for path in ("hybrid", "semantic", "bm25"):
                st = stores[t]
                sc = scoped(ids if bug != "no_scope" else None)
                if path == "hybrid":
                    hits = hybrid_search(st, q, limit=50, pool=200, vector_threshold=-1.0, document_ids=sc, namespace=t)
                elif path == "semantic":
                    hits = st.semantic_search(q, top_k=200, threshold=-1.0, document_ids=sc)
                else:
                    hits = st.bm25_search(q, limit=200, document_ids=sc)
                got = {h["document_id"] for h in hits}
                out.check(got <= permitted, f"{path}: {t} was handed {sorted(got - permitted)[:2]}, which its access list does not include")
                context = "\n".join(h["text"] for h in hits)
                leaked = [c for c in all_secrets if c in context and c not in permitted_secrets]
                out.check(not leaked, f"{path}: the context assembled for {t} contains a secret it may not read: {leaked[:2]}")
                if not permitted:
                    out.check(not hits, f"{path}: an empty access list returned {len(hits)} hits")
        # an entitled query finds its own document (counted for false denials)
        for d in rng.sample(sorted(permitted), min(2, len(permitted))):
            hits = hybrid_search(stores[t], corpus[t][d], limit=10, pool=200, vector_threshold=-1.0, document_ids=scoped(ids if bug != "no_scope" else None), namespace=t)
            legit_checked += 1
            out.legit(any(h["document_id"] == d for h in hits), f"{t} could not retrieve its permitted document {d} by its own secret")

    # the namespace guard: the wrong namespace, or none, never returns data
    for t in tenants:
        for ns in [o for o in tenants if o != t] + [None, "", "ACME"]:
            try:
                hybrid_search(stores[t], rng.choice(COMMON), namespace=ns, vector_threshold=-1.0)
                refused = False
            except NamespaceMismatch:
                refused = True
            out.check(refused, f"a request for namespace {ns!r} was answered by the store of {t}")
    # the graph guard, same property
    from redevops_rag import ChangeClosureGraph, Node
    g = ChangeClosureGraph([Node("s", "x"), Node("a", "y")], {"a": {"s"}}, namespace="acme")
    for ns in ("globex", None):
        try:
            g.retrieve("s", 10, namespace=ns)
            ok = False
        except NamespaceMismatch:
            ok = True
        out.check(ok, f"the graph of acme answered a request for namespace {ns!r}")

    # what the library leaves to the caller: an unguarded store shared by all tenants and a caller that forgets to scope
    t = rng.choice(tenants)
    foreign_docs = set()
    for c, (ot, d) in all_secrets.items():
        if ot != t:
            foreign_docs |= {h["document_id"] for h in shared.bm25_search(c, limit=5) if h["document_id"] == d}
    out.meta = {"queries_entitled": legit_checked, "unscoped_shared_store_returns_other_tenants_docs": len(foreign_docs), "bug": bug or None,
                "boundary": "an unguarded, unscoped store has no isolation; it is the caller's job (redevops-rag README)"}
    return out


class _FakeIndex:
    """What a RagStore is to RagIndexer: indexes a folder, searches what it indexed. Two pairs handed the same file share one of these, as they would share one DuckDB file."""
    def __init__(self, path: str):
        self.path, self.texts = path, []

    def index(self, root: str) -> dict:
        for p in sorted(Path(root).rglob("*.txt")):
            self.texts.append(p.read_text())
        return {"chunks": len(self.texts)}

    def search(self, query: str, k: int = 5):
        return [{"filename": "f.txt", "text": t} for t in self.texts[:k]]


AWKWARD = ["acme", "a", "b", "a_b", "b_c", "c", "a:b", "b:c", "..", "../x", "a/b", "x/..", "a b", " a", "ACME", "Acme", "acme.", "α", "á", "a\x00b", "x" * 120, "con", "p-1", "p_1"]


def s10_rag_indexer_identity(seed: int, tmp: Path) -> Result:
    from agentic_os.sources_rag import RagIndexer
    rng = random.Random(seed)
    out = Result()
    bug = os.environ.get("GOVERNED_INDEXER_BUG", "")
    d = tmp / f"s10_{seed}"
    d.mkdir(parents=True, exist_ok=True)
    db_dir = d / "idx"
    by_path: dict = {}

    def factory(path: str):
        return by_path.setdefault(path, _FakeIndex(path))
    ix = RagIndexer(db_dir=str(db_dir), rag_factory=factory)
    if bug == "join":
        def _naive(project_id, source_id):
            return os.path.join(str(db_dir), f"{project_id}_{source_id}.duckdb")
        ix._path = _naive                                  # type: ignore[method-assign]
        orig_store = ix._store

        def _store(project_id, source_id):                  # the pre-fix store: keyed by the path it computed
            p = _naive(project_id, source_id)
            os.makedirs(str(db_dir), exist_ok=True)
            return factory(p)
        ix._store = _store                                  # type: ignore[method-assign]
        _ = orig_store
    pairs = list({(rng.choice(AWKWARD), rng.choice(AWKWARD)) for _ in range(rng.randint(30, 45))})
    paths: dict = {}
    refused = 0

    def mark(p, s):
        return "mk" + hashlib.sha256(json.dumps([p, s]).encode()).hexdigest()[:20]
    src = d / "src"
    for i, (p, s) in enumerate(pairs):
        folder = src / f"{i}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "a.txt").write_text(mark(p, s))
        try:
            ix.index(p, s, [str(folder / "a.txt")])
        except ValueError:
            refused += 1                                    # a refusal of an id that cannot name an index is fine; what must never happen is a shared or escaping file
            continue
        paths[(p, s)] = next(k for k, v in by_path.items() if mark(p, s) in v.texts)
    resolved = {k: v for k, v in paths.items()}
    base = os.path.abspath(str(db_dir))
    for (p, s), path in resolved.items():
        out.check(os.path.dirname(os.path.abspath(path)) == base, f"the index of {(p, s)!r} is outside the index directory: {path}")
    seen: dict = {}
    for k, path in resolved.items():
        key = os.path.abspath(path).casefold()              # a case-insensitive filesystem (macOS, Windows) treats these as one file
        out.check(key not in seen or seen[key] == k, f"{k!r} and {seen.get(key)!r} share the index file {os.path.basename(path)}")
        seen.setdefault(key, k)
    for (p, s) in resolved:
        ev = ix.retrieve(p, s, "q", k=50)
        marks = {r.summary for r in ev.refs}
        out.check(all(mark(p, s) in m for m in marks), f"retrieval for {(p, s)!r} returned content that was indexed by another pair: {sorted(marks)[:2]}")
        out.legit(bool(marks), f"{(p, s)!r} retrieved nothing from its own index")
    for k, path in resolved.items():
        again = os.path.abspath(ix._path(*k)) if bug != "join" else path
        out.check(again == os.path.abspath(path), f"the index file of {k!r} changed between calls")
    out.meta = {"pairs": len(pairs), "refused": refused, "bug": bug or None}
    return out
