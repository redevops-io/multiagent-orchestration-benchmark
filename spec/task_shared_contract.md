# Task shape: SHARED-CONTRACT (pilot task)

Port the canonical cross-boundary schemas + serialization from Context Runtime to Kotlin. Chosen as
the pilot because it is the highest integration-stress / medium-parallelism shape: several worker
subtasks must agree on one contract, so it exercises exactly the merge/integration difference.

## Mandatory (gated)
1. Kotlin data classes for: `Goal`, `Constraint`, `Capability`, `EvidenceArtifact`,
   `RetrievalRequest`, `RetrievalResult`, and a `Provenance` value object — field names/shapes match
   the frozen Python source (contextos@3473990) representations.
2. Deterministic JSON serialization + deserialization with **round-trip identity**
   (`decode(encode(x)) == x`) for every type, incl. nested/optional fields and collections.
3. A single `Serde` interface used by all types (no per-type ad-hoc JSON) — the shared contract.
4. Validation: reject malformed inputs (missing required fields, wrong types) with typed errors,
   not exceptions-as-control-flow.
5. Two downstream consumers that depend on the schemas WITHOUT importing each other:
   a `RetrievalRequest` builder and an `EvidenceArtifact` provenance-merger — proving the contract is
   the only coupling.

## Optional (credit, not gated)
- kotlinx.serialization vs hand-rolled — either allowed if round-trip holds.
- Sealed-class modelling of `Constraint` variants; value classes for ids.

## Prohibited (architecture gate fails if violated)
- No consumer module importing another consumer directly (only the shared contract).
- No reflection-based "stringly-typed" JSON that bypasses the `Serde` contract.
- No network / filesystem access in the schema or serialization layer.

## Hidden acceptance tests (not shown to workers; run at the gate)
- Round-trip identity on a fixture corpus of 30 hand-authored + edge-case instances (empty
  collections, unicode, max/min numerics, absent optionals).
- Malformed-input rejection: 12 negative fixtures each yield a typed validation error (no crash).
- Contract-only-coupling: static import check — neither consumer imports the other.
- Cross-instance provenance merge: associativity + no-duplicate-source invariant on 8 fixtures.

## Injection (main run only, this shape): incompatible-shared-contract
Two workers are briefed to implement overlapping parts of the `Serde`/`Provenance` contract with a
deliberately divergent assumption (e.g. timestamp as epoch-millis vs ISO-8601). Measures whether each
arm detects the contract conflict at integration and resolves it without a silent, wrong merge.
