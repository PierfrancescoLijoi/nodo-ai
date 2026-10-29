# Benchmark artifacts, 23 October 2026

Produced by `eval/run_queries.py` (questions, four languages, `-repeat` is the second pass) and
`tools/speech-bench/bench_whisper.py` + `quality_report.py` (speech, Whisper medium, float16, RTX 4050).
`memory-query*` is the same set on the mock retriever and model: it proves the wiring and the
isolation of workspaces and deleted text, not quality. Corpus: one synthetic English meeting in nine
audio conditions (Windows voice, `tools/speech-bench/stress_corpus.py`) and the 60-case golden set in
`eval/golden_set`. Interpretation and limits: section 10 of `docs/BENCHMARK-PLAN.md`.
