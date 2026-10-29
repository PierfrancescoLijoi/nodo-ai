"""Record, process and question a meeting through the real local engine over HTTP, with timings.

    python tools/local-check/e2e_http.py <audio file> [locale] [data dir]

Starts the engine exactly as the desktop app does (real models, a throw-away data folder unless one
is given), uploads the file the way the app does, waits for the pipeline, prints how long each state
took, then reads the result and asks a question. Nothing leaves this computer.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx

from nodo_api.desktop import Engine, choose_port


def main() -> None:
    audio = Path(sys.argv[1])
    locale = sys.argv[2] if len(sys.argv) > 2 else "en"
    folder = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp(prefix="nodo-e2e-"))
    folder.mkdir(parents=True, exist_ok=True)
    engine = Engine(folder, None, choose_port(0))
    started = time.perf_counter()
    engine.start()
    print(f"engine up in {time.perf_counter() - started:.1f} s on {engine.url}")
    headers = {"Authorization": f"Bearer {engine.token}"}
    api = httpx.Client(base_url=engine.url, headers=headers, timeout=900)
    try:
        print("status:", api.get("/v1/local/status").json())
        attestation = {
            "notice_version": "2026-10-v1",
            "locale": locale,
            "retention_policy_id": "retention-30d",
            "acknowledged": True,
        }
        meeting = api.post(
            "/v1/meetings",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={"recording_attestation": attestation, "title": audio.stem},
        ).json()
        meeting_id = meeting["id"]
        session = api.post(f"/v1/meetings/{meeting_id}/upload-sessions").json()
        t0 = time.perf_counter()
        put = httpx.put(session["upload_url"], content=audio.read_bytes(), timeout=300)
        print(f"upload {audio.stat().st_size / 1e6:.1f} MB: {put.status_code} in {time.perf_counter() - t0:.1f} s")
        api.post(f"/v1/meetings/{meeting_id}/complete")

        gaps: list[float] = []

        def heartbeat() -> None:
            # how long does a trivial request take while the engine is busy with the recording?
            probe = httpx.Client(base_url=engine.url, timeout=60)
            while not gaps or gaps[-1] >= 0:
                start = time.perf_counter()
                probe.get("/healthz")
                gaps.append(time.perf_counter() - start)
                time.sleep(0.5)

        threading.Thread(target=heartbeat, daemon=True).start()
        t0, last, marks = time.perf_counter(), "", {}
        while True:
            state = api.get(f"/v1/meetings/{meeting_id}").json()["state"]
            if state != last:
                marks[state] = time.perf_counter() - t0
                print(f"  +{marks[state]:6.1f} s  state {state}")
                last = state
            if state in ("review", "ready", "failed"):
                break
            time.sleep(1)

        gaps.append(-1)  # stop the heartbeat
        worst = max(g for g in gaps if g >= 0)
        print(f"slowest /healthz while processing: {worst * 1000:.0f} ms over {len(gaps) - 1} probes")
        detail = api.get(f"/v1/meetings/{meeting_id}").json()
        print("title:", detail["title"], "| duration ms:", detail["duration_ms"])
        print("summary:", detail["summary"])
        print("speakers:", [(s["id"], s["segments"], s["talk_ms"]) for s in detail["speakers"]])
        insights = detail["insights"]
        print("people:", [p["label"] for p in insights["people"]])
        print("decisions:", [(d["label"], d["status"]) for d in insights["decisions"]])
        print("actions:", [(a["label"], a["owner_label"]) for a in insights["actions"]])
        question = "What did we decide?" if locale == "en" else "Cosa abbiamo deciso?"
        t0 = time.perf_counter()
        answer = api.post("/v1/query", json={"question": question, "ui_locale": locale}).json()
        print(f"question answered in {time.perf_counter() - t0:.1f} s: {answer.get('answerability')}")
        print("answer:", answer.get("answer"))
    finally:
        api.close()
        engine.stop()


if __name__ == "__main__":
    main()
