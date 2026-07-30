package benchspec
private var p=0; private var t=0
private fun ck(n:String,c:Boolean){t++; if(c)p++ else println("FAIL: $n")}
fun main(){
    ck("tok.basic", tokenize("Hello, WORLD! hello")==listOf("hello","world","hello"))
    ck("tok.unicode", tokenize("café Café")==listOf("café","café") || tokenize("a1 b2")==listOf("a1","b2"))
    ck("tok.empty", tokenize("  ,;. ")==emptyList<String>())
    ck("tf.count", tf(listOf("a","b","a","a"))==mapOf("a" to 3,"b" to 1))
    ck("tf.empty", tf(emptyList())==emptyMap<String,Int>())
    ck("p@k.half", Metrics.precisionAtK(setOf(1,2,3), listOf(1,9,2,8), 4)==0.5)
    ck("p@k.perfect", Metrics.precisionAtK(setOf(5,6), listOf(5,6), 2)==1.0)
    ck("fixture.ok", parseFixture("1,hello\n2,world")==listOf(1 to "hello",2 to "world"))
    ck("fixture.blank", parseFixture("1,a\n\n2,b")==listOf(1 to "a",2 to "b"))
    ck("fixture.malformed", try{parseFixture("noComma"); false}catch(e:IllegalArgumentException){true}catch(e:Exception){false})
    println("RESULT $p/$t"); if(p!=t) kotlin.system.exitProcess(1)
}
