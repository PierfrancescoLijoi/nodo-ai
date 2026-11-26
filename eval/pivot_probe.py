"""Does translating passages to English (a pivot) improve cross-language retrieval?

    services/api/.venv/Scripts/python eval/pivot_probe.py out.json
Writes {segment_id: english_translation} using the installed local language model.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_queries import ROOT, build_adapters  # noqa: E402

meetings = json.loads((ROOT / "golden_set/fixtures/meetings.json").read_text())
meetings = meetings if isinstance(meetings, list) else meetings["meetings"]
out = {}
with tempfile.TemporaryDirectory(prefix="piv-", dir=ROOT.parent / "tmp", ignore_cleanup_errors=True) as folder:
    _, adapters = build_adapters("local", Path(folder))
    llm = adapters.llm
    with llm.lease():
        client = llm.client()
        for m in meetings:
            for s in m["segments"]:
                if m["spoken_language"] == "en" or s.get("tombstoned"):
                    out[s["id"]] = s["text"]
                    continue
                reply = client.chat.completions.create(
                    model="local",
                    temperature=0,
                    max_tokens=120,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                    messages=[
                        {"role": "system", "content": "Translate the user's sentence to English. Keep names, numbers and technical terms. Output only the translation."},
                        {"role": "user", "content": s["text"]},
                    ],
                )
                out[s["id"]] = reply.choices[0].message.content.strip()
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1))
