package benchspec
private var pass = 0; private var total = 0
private fun check(name: String, cond: Boolean) { total++; if (cond) pass++ else println("FAIL: $name") }
private fun <T> rt(name: String, x: T, enc: (T)->String, dec: (String)->T) =
    check(name, dec(enc(x)) == x)
fun main() {
    rt("goal.basic", Goal("ship it", listOf("fast","cheap")), Serde::encode, Serde::decodeGoal)
    rt("goal.empty", Goal("", emptyList()), Serde::encode, Serde::decodeGoal)
    rt("goal.unicode", Goal("naïve café — 日本語", listOf("prio:1")), Serde::encode, Serde::decodeGoal)
    rt("cap.basic", Capability("rag", "1.4.0"), Serde::encode, Serde::decodeCapability)
    rt("ev.basic", EvidenceArtifact("db","row-1", listOf("q1","fuse")), Serde::encode, Serde::decodeEvidence)
    rt("ev.emptyprov", EvidenceArtifact("api","x", emptyList()), Serde::encode, Serde::decodeEvidence)
    // malformed -> typed SerdeError
    check("malformed.goal", try { Serde.decodeGoal("{not json"); false } catch (e: SerdeError) { true } catch (e: Exception) { false })
    check("malformed.cap",  try { Serde.decodeCapability("{}"); false } catch (e: SerdeError) { true } catch (e: Exception) { false })
    println("RESULT $pass/$total")
    if (pass != total) kotlin.system.exitProcess(1)
}
