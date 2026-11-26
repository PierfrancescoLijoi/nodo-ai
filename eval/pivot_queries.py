import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_queries import ROOT, build_adapters  # noqa: E402

cases = [json.loads(l) for l in (ROOT / "golden_set/cases.jsonl").read_text().splitlines()]
out = {}
with tempfile.TemporaryDirectory(prefix="piv-", dir=ROOT.parent / "tmp", ignore_cleanup_errors=True) as folder:
    _, adapters = build_adapters("local", Path(folder))
    llm = adapters.llm
    with llm.lease():
        client = llm.client()
        for c in cases:
            for loc, q in (c["question"].items() if isinstance(c["question"], dict) else []):
                if loc == "en":
                    continue
                r = client.chat.completions.create(model="local", temperature=0, max_tokens=80,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                    messages=[{"role": "system", "content": "Translate the user's question to English. Keep names and technical terms. Output only the translation."},
                              {"role": "user", "content": q}])
                out[f"{c['id']}|{loc}"] = r.choices[0].message.content.strip()
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1))
