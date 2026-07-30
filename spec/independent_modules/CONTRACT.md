# independent-modules — four INDEPENDENT utilities (package benchspec). High parallelism, low integration stress.
    fun tokenize(s: String): List<String>          // lowercase; split on non-alphanumeric; drop empties
    fun tf(tokens: List<String>): Map<String, Int>  // term -> frequency
    object Metrics { fun precisionAtK(relevant: Set<Int>, ranked: List<Int>, k: Int): Double }  // |relevant ∩ ranked[0until k]| / k
    fun parseFixture(csv: String): List<Pair<Int, String>>  // lines "id,text" -> (id,text); skip blank lines; malformed line -> throw IllegalArgumentException
