"""End-to-end smoke test of a running NODO API: the whole product path through the public door.

    export NODO_SMOKE_TOKEN=<an access token for the API>
    python tools/smoke/smoke.py https://api.example.com                  # silence: expects the failure path
    python tools/smoke/smoke.py https://api.example.com --audio talk.wav # real speech: expects review

It records a meeting, uploads audio the way the apps do (blob link, blocks), waits for processing,
reads the transcript, asks a question, reads the graph, exports the data, and deletes the meeting.
With the default generated silence the right outcome is a *failed* meeting (no speech): that proves
upload, queue, worker, speech and failure handling without needing a recording. With real speech
the right outcome is `review`, which also proves transcription, indexing, the models and the graph.
Nothing is printed except step names, statuses and ids. The token is read from the environment."""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass

import httpx

BLOCK = 4 * 1024 * 1024
SINGLE_UPLOAD_LIMIT = 200 * 1024 * 1024
ATTESTATION = {
    "notice_version": "2026-10-v1",
    "locale": "en",
    "retention_policy_id": "retention-7d",
    "acknowledged": True,
}


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def silence_wav(seconds: int = 3, rate: int = 16_000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)
    return out.getvalue()


class Smoke:
    def __init__(
        self,
        client: httpx.Client,
        token: str,
        *,
        audio: bytes | None = None,
        expect: str | None = None,
        timeout: float = 1800.0,
        poll: float = 5.0,
        keep: bool = False,
        blob_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self._audio = audio if audio is not None else silence_wav()
        self._expect = expect or ("review" if audio is not None else "failed")
        self._timeout, self._poll, self._keep, self._sleep = timeout, poll, keep, sleep
        self._blob_client = blob_client
        self.steps: list[Step] = []
        self._meeting = ""

    # ------------------------------------------------------------------------------- plumbing

    def _do(self, name: str, action: Callable[[], str]) -> bool:
        try:
            detail = action()
        except AssertionError as failure:
            self.steps.append(Step(name, False, str(failure)))
            return False
        except httpx.HTTPError as error:
            self.steps.append(Step(name, False, f"{type(error).__name__}"))
            return False
        self.steps.append(Step(name, True, detail))
        return True

    def _call(self, method: str, path: str, expect: tuple[int, ...] = (200,), **kw) -> httpx.Response:
        headers = {**self._headers, **kw.pop("headers", {})}
        response = self._http.request(method, path, headers=headers, **kw)
        assert response.status_code in expect, f"{method} {path.split('?')[0]} -> {response.status_code}"
        return response

    # ------------------------------------------------------------------------------- the path

    def run(self) -> bool:
        ok = self._do("health", self._health) and self._do("auth", self._auth)
        ok = ok and self._do("record", self._record) and self._do("upload", self._upload)
        ok = ok and self._do("process", self._process)
        if ok and self._expect == "review":
            for name, action in (
                ("transcript", self._transcript),
                ("question", self._question),
                ("graph", self._graph),
            ):
                ok = self._do(name, action) and ok
        if self._meeting:
            ok = self._do("export", self._export) and ok
            if not self._keep:
                ok = self._do("delete", self._delete) and ok
        return ok and all(s.ok for s in self.steps)

    def _health(self) -> str:
        assert self._http.get("/healthz").status_code == 200, "GET /healthz did not answer 200"
        ready = self._http.get("/readyz")
        assert ready.status_code == 200, f"GET /readyz -> {ready.status_code}"
        return "alive and ready"

    def _auth(self) -> str:
        self._call("GET", "/v1/meetings?limit=1")
        return "the token is accepted"

    def _record(self) -> str:
        created = self._call(
            "POST",
            "/v1/meetings",
            (201,),
            headers={"Idempotency-Key": f"smoke-{uuid.uuid4().hex}"},
            json={"recording_attestation": ATTESTATION, "title": "NODO smoke test"},
        )
        self._meeting = created.json()["id"]
        return f"meeting {self._meeting}"

    def _upload(self) -> str:
        session = self._call("POST", f"/v1/meetings/{self._meeting}/upload-sessions", (200, 201))
        url = session.json()["upload_url"]
        azure = "blob.core.windows.net" in url
        # The link is the credential: no bearer token goes to the blob service.
        blob = self._blob_client or httpx.Client(timeout=120.0)
        try:
            if not azure:
                local = self._http.put(url, content=self._audio)
                assert local.status_code == 204, f"upload -> {local.status_code}"
            elif len(self._audio) <= SINGLE_UPLOAD_LIMIT:
                put = blob.put(url, content=self._audio, headers={"x-ms-blob-type": "BlockBlob"})
                assert put.status_code == 201, f"blob upload -> {put.status_code}"
            else:
                self._blocks(blob, url)
        finally:
            if self._blob_client is None:
                blob.close()
        self._call("POST", f"/v1/meetings/{self._meeting}/complete")
        return f"{len(self._audio)} bytes sent"

    def _blocks(self, blob: httpx.Client, url: str) -> None:
        ids = []
        for number, start in enumerate(range(0, len(self._audio), BLOCK)):
            block_id = base64.b64encode(f"{number:08d}".encode()).decode()
            ids.append(block_id)
            put = blob.put(f"{url}&comp=block&blockid={block_id}", content=self._audio[start : start + BLOCK])
            assert put.status_code == 201, f"block {number} -> {put.status_code}"
        listing = "".join(f"<Latest>{i}</Latest>" for i in ids)
        done = blob.put(f"{url}&comp=blocklist", content=f"<BlockList>{listing}</BlockList>")
        assert done.status_code == 201, f"block list -> {done.status_code}"

    def _process(self) -> str:
        deadline = time.monotonic() + self._timeout
        state = ""
        while time.monotonic() < deadline:
            state = self._call("GET", f"/v1/meetings/{self._meeting}").json()["state"]
            if state in ("review", "ready", "failed"):
                break
            self._sleep(self._poll)
        finished = "review" if state in ("review", "ready") else state
        assert finished == self._expect, f"ended as {state!r}, expected {self._expect!r}"
        return f"ended as {state}, as expected"

    def _transcript(self) -> str:
        body = self._call("GET", f"/v1/meetings/{self._meeting}/transcript").json()
        assert body["total"] > 0, "the transcript is empty"
        return f"{body['total']} segments"

    def _question(self) -> str:
        reply = self._call(
            "POST", "/v1/query", json={"question": "What was discussed?", "ui_locale": "en"}
        ).json()
        assert reply["answerability"] in ("supported", "partial", "unanswerable")
        note = " (model unavailable: sources only)" if reply.get("degraded") else ""
        return f"answerability {reply['answerability']}{note}"

    def _graph(self) -> str:
        body = self._call("GET", "/v1/graph/subgraph?depth=1").json()
        return f"{len(body.get('nodes', []))} nodes"

    def _export(self) -> str:
        job = self._call("POST", "/v1/export", (202,), json={"meeting_id": self._meeting}).json()
        deadline = time.monotonic() + 300
        status = job
        while time.monotonic() < deadline and status["status"] not in ("ready", "failed"):
            self._sleep(self._poll)
            status = self._call("GET", f"/v1/export/{job['job_id']}").json()
        assert status["status"] == "ready", f"export ended as {status['status']!r}"
        document = self._call("GET", status["download_path"]).json()
        assert [m["id"] for m in document["meetings"]] == [self._meeting], "export holds another meeting"
        return "built, downloaded, holds exactly this meeting"

    def _delete(self) -> str:
        self._call("DELETE", f"/v1/meetings/{self._meeting}", (200, 204))
        self._call("GET", f"/v1/meetings/{self._meeting}", (404,))
        return "erased"


def render(steps: list[Step]) -> str:
    lines = [f"[{'ok' if s.ok else 'FAIL'}] {s.name:<11} {s.detail}" for s in steps]
    lines.append(f"{len(steps)} steps, {sum(1 for s in steps if not s.ok)} failed")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base_url")
    parser.add_argument("--audio", help="a WAV file with speech; default is generated silence")
    parser.add_argument("--expect", choices=("review", "failed"))
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--keep", action="store_true", help="do not delete the test meeting")
    args = parser.parse_args(argv)
    token = os.environ.get("NODO_SMOKE_TOKEN", "")
    if not token:
        print("Set NODO_SMOKE_TOKEN to an access token for the API.", file=sys.stderr)  # noqa: T201
        return 2
    audio = open(args.audio, "rb").read() if args.audio else None  # noqa: SIM115
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60.0) as client:
        smoke = Smoke(
            client, token, audio=audio, expect=args.expect, timeout=args.timeout, keep=args.keep
        )
        passed = smoke.run()
    print(render(smoke.steps))  # noqa: T201
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
