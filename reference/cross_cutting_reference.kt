package benchspec
interface Retriever { fun retrieve(query: String, k: Int): List<String> }
private fun toks(s:String)=s.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter{it.isNotEmpty()}.toSet()
class RagRetriever(private val corpus: List<String>) : Retriever {
    override fun retrieve(query: String, k: Int): List<String> { val q=toks(query); return corpus.sortedByDescending{ d -> q.count{it in toks(d)} }.take(k) } }
class Planner(private val retriever: Retriever) { fun answer(query: String): String = retriever.retrieve(query,1).firstOrNull() ?: "no result" }
