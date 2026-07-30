package benchspec
import java.util.Base64
data class Goal(val text: String, val constraints: List<String>)
data class Capability(val id: String, val version: String)
data class EvidenceArtifact(val source: String, val content: String, val provenance: List<String>)
class SerdeError(msg: String) : Exception(msg)
object Serde {
    private fun e(s: String) = Base64.getEncoder().encodeToString(s.toByteArray(Charsets.UTF_8))
    private fun d(s: String): String = try { String(Base64.getDecoder().decode(s), Charsets.UTF_8) } catch (x: Exception) { throw SerdeError("bad b64") }
    private fun eList(xs: List<String>) = xs.joinToString(",") { e(it) }
    private fun dList(s: String) = if (s.isEmpty()) emptyList() else s.split(",").map { d(it) }
    private fun parts(s: String, tag: String, n: Int): List<String> {
        val f = s.split("|"); if (f.size != n + 1 || f[0] != tag) throw SerdeError("bad $tag"); return f.drop(1) }
    fun encode(g: Goal) = "G|${e(g.text)}|${eList(g.constraints)}"
    fun decodeGoal(s: String): Goal { val p = parts(s,"G",2); return Goal(d(p[0]), dList(p[1])) }
    fun encode(c: Capability) = "C|${e(c.id)}|${e(c.version)}"
    fun decodeCapability(s: String): Capability { val p = parts(s,"C",2); return Capability(d(p[0]), d(p[1])) }
    fun encode(x: EvidenceArtifact) = "E|${e(x.source)}|${e(x.content)}|${eList(x.provenance)}"
    fun decodeEvidence(s: String): EvidenceArtifact { val p = parts(s,"E",3); return EvidenceArtifact(d(p[0]), d(p[1]), dList(p[2])) }
}
