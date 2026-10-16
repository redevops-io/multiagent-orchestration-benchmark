# Contributing

Issues and pull requests are welcome: <https://github.com/redevops-io/multiagent-orchestration-benchmark/issues>.

- The design holds everything constant except the orchestration layer. Changes to the frozen configuration (`spec/frozen_config.json`) or the gates (`spec/gates_and_rubric.md`) alter the experiment; describe them as a new run, not an edit of the existing results.
- Re-check the gates with `python3 validate_gates.py` (needs `kotlinc` and JDK 17+; no API keys).
- API keys come from environment variables only. Never commit keys; CI runs a secret scan (`.github/workflows/secret-scan.yml`).
- Contributions are accepted under the repository's licence (`LICENSE`, MIT).

Security reports: see [SECURITY.md](SECURITY.md).
