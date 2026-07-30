# Pilot contract (reduced shared-contract task) — the exact Kotlin surface workers must produce
Package `benchspec`. A single integrated `solution.kt` must declare:

    data class Goal(val text: String, val constraints: List<String>)
    data class Capability(val id: String, val version: String)
    data class EvidenceArtifact(val source: String, val content: String, val provenance: List<String>)

    // one shared Serde contract used by ALL types (no per-type ad-hoc JSON):
    object Serde {
        fun encode(g: Goal): String;  fun decodeGoal(s: String): Goal
        fun encode(c: Capability): String;  fun decodeCapability(s: String): Capability
        fun encode(e: EvidenceArtifact): String;  fun decodeEvidence(s: String): EvidenceArtifact
    }
    class SerdeError(msg: String) : Exception(msg)   // typed error for malformed input

Round-trip identity must hold: decodeX(encode(x)) == x for all types, incl. empty lists & unicode.
Malformed input (not valid JSON / missing required field) must throw SerdeError, not crash otherwise.
