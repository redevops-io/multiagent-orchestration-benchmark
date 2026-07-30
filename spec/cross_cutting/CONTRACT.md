# cross-cutting-refactor — route retrieval through a Capability interface (package benchspec). Low parallelism, high integration/architecture stress.
    interface Retriever { fun retrieve(query: String, k: Int): List<String> }   // the Capability boundary
    class RagRetriever(private val corpus: List<String>) : Retriever            // ranks corpus by query-token overlap, returns top-k
    class Planner(private val retriever: Retriever) { fun answer(query: String): String }  // MUST use the INJECTED retriever (the Capability), never construct RagRetriever itself; answer returns the top retrieved doc or "no result"
