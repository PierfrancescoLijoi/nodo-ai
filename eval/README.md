# Golden-set evaluation

The fixture set exercises grounded answers across meetings, languages, projects, deletion state, and workspace boundaries. The data is fictional and stable so CI results can be compared over time.

Metrics follow the product blueprint:

- Citation precision: the share of returned citations that match the annotated supporting segments. The beta target is at least 95 percent.
- Answer faithfulness: the share of answer claims entailed by cited, non-deleted segments. The beta target is at least 95 percent.
- Cross-meeting fact accuracy: correctness on temporal changes and facts assembled across meetings. The beta target is at least 90 percent.
- ACL leak count: citations or answer content recovered from a workspace unavailable to the caller. The required value is 0.

Validate the static data with only the Python standard library:

```powershell
python eval/validate_golden_set.py
```

`run_queries.py` loads the fixtures into the real stores, asks every case in the chosen language through the query service and writes predictions that `score_results.py` compares with the expected and forbidden lists (`memory` for the wiring, `local` for the models the desktop app uses; a second pass measures the answer cache). Deleted segments and segments from other workspaces must never be returned. Results: `docs/benchmarks/`.

The local adapter can experimentally rerank the top 20 ACL-filtered hybrid candidates with the
small multilingual ONNX cross-encoder. It is disabled by default. Certify it with an A/B run for
every language; never accept a
latency improvement or an aggregate gain that introduces a forbidden citation or regresses a
language/category:

```powershell
$env:NODO_LOCAL_RERANKER_ENABLED="false"
python eval/run_queries.py local tmp/results/no-reranker-en.jsonl en
python eval/score_results.py eval/golden_set/cases.jsonl tmp/results/no-reranker-en.jsonl

$env:NODO_LOCAL_RERANKER_ENABLED="true"
python eval/run_queries.py local tmp/results/reranker-en.jsonl en
python eval/score_results.py eval/golden_set/cases.jsonl tmp/results/reranker-en.jsonl
```

Repeat for `it`, `pl` and `da`, with a fresh process per setting. Record citation precision/recall,
answerability and p50/p95/max latency together; the reranker stays enabled only if all four language
guardrails hold. Its isolated CPU cost is reproducible with
`python services/api/scripts/bench_reranker.py 20 100`.

## Document import measurement

`eval/documents/files` holds ten fictional documents (companies, people and projects are invented)
in English, Italian, Polish and Danish, as Markdown, text, HTML, DOCX and text-layer PDF. The DOCX
and PDF files were generated once with neutral metadata. `expected.jsonl` annotates, per file, the
people, projects, systems, decisions and actions a reader would extract, with `match` terms
compared against normalised labels, plus the expected `ASSIGNED_TO`, `PROPOSED_BY` and
`DEPENDS_ON` relations; entries marked `optional` are accepted but not required.
`meetings_expected.jsonl` annotates the golden-set meetings the same way, as the meeting baseline.

```powershell
$env:NODO_TEST_LOCAL_MODELS="1"
services/api/.venv/Scripts/python eval/measure_import.py gpu
services/api/.venv/Scripts/python eval/measure_import.py cpu
```

Run each device in its own invocation: the meeting baseline runs only on the first device of a call.

The script imports every file through `SourceImportService` on a fresh local engine and prints
seconds per 50 pages (a page is 3 000 parsed characters) for parsing, indexing and extraction,
peak memory, and entity and relation precision and recall next to the meeting baseline. It needs
the installed local models and never downloads anything. Results: `docs/PERFORMANCE.md`.
