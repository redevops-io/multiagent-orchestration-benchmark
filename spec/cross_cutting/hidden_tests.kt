package benchspec
private var p=0; private var t=0
private fun ck(n:String,c:Boolean){t++; if(c)p++ else println("FAIL: $n")}
private class FakeRetriever: Retriever { override fun retrieve(query:String,k:Int)=listOf("SENTINEL-$query") }
fun main(){
    ck("planner.uses.injected.capability", Planner(FakeRetriever()).answer("q")=="SENTINEL-q")  // boundary: must use injected Retriever
    val rag=RagRetriever(listOf("the cat sat","a dog ran"))
    ck("rag.topk", rag.retrieve("cat",1)==listOf("the cat sat"))
    ck("rag.k", rag.retrieve("cat dog",2).size==2)
    ck("planner.with.rag", Planner(rag).answer("dog").contains("dog"))
    ck("planner.empty", Planner(object:Retriever{override fun retrieve(query:String,k:Int)=emptyList<String>()}).answer("x")=="no result")
    println("RESULT $p/$t"); if(p!=t) kotlin.system.exitProcess(1)
}
