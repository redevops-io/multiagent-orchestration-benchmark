package benchspec
fun tokenize(s: String): List<String> = s.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.isNotEmpty() }
fun tf(tokens: List<String>): Map<String,Int> { val m=LinkedHashMap<String,Int>(); for(x in tokens) m[x]=(m[x]?:0)+1; return m }
object Metrics { fun precisionAtK(relevant: Set<Int>, ranked: List<Int>, k: Int): Double = ranked.take(k).count{it in relevant}.toDouble()/k }
fun parseFixture(csv: String): List<Pair<Int,String>> = csv.split("\n").filter{it.isNotBlank()}.map{ l -> val i=l.indexOf(','); if(i<0) throw IllegalArgumentException("bad: $l"); l.substring(0,i).trim().toInt() to l.substring(i+1) }
