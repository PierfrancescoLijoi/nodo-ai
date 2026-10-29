#!/usr/bin/env bash
# Starts the real API image in Azure mode against a PostgreSQL you give it, and checks what can be
# checked without Azure: every migration applies to an empty database, the app starts under the
# production process settings (two workers), /healthz and /readyz answer, security headers are on,
# and an unauthenticated call is refused with the standard error body.
#
#   tools/verify/container-check.sh <image> <network> [database-name]
#     <network> = "host" (CI, Postgres on localhost:5432) or "container:<postgres-container>" (local)
#
# Postgres must accept user postgres / password postgres. Everything Azure-specific (Entra tokens,
# private endpoints, models) is what the preflight command checks once the real thing exists.
set -euo pipefail

IMAGE="${1:?image}"
NETWORK="${2:?network}"
DATABASE="${3:-nodo_verify}"
NAME="nodo-api-verify-$$"
PORT_NOTE="8000"

env_args=(
  -e NODO_ENVIRONMENT=test -e NODO_BACKEND=azure -e NODO_AUTH_MODE=oidc
  -e NODO_OIDC_ISSUER=https://issuer.example.test/v2.0 -e NODO_OIDC_AUDIENCE=api://nodo
  -e NODO_ATTESTATION_HMAC_KEY=verify-attestation-key-0123456789-0123456789
  -e NODO_AZURE_STORAGE_ACCOUNT=stverify
  -e NODO_AZURE_SERVICEBUS_FQDN=sb-verify.servicebus.windows.net
  -e NODO_AZURE_POSTGRES_HOST=127.0.0.1 -e "NODO_AZURE_POSTGRES_DATABASE=${DATABASE}"
  -e NODO_AZURE_POSTGRES_USER=postgres -e NODO_AZURE_POSTGRES_PASSWORD=postgres
  -e NODO_AZURE_SEARCH_ENDPOINT=https://srch-verify.search.windows.net
  -e NODO_AZURE_OPENAI_ENDPOINT=https://oai-verify.openai.azure.com/
  -e NODO_AZURE_SPEECH_ENDPOINT=https://speech-verify.cognitiveservices.azure.com/
  -e NODO_AZURE_ENVIRONMENT=verify -e WEB_CONCURRENCY=2
)

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== migrations on an empty database"
docker run --rm --network "$NETWORK" "${env_args[@]}" "$IMAGE" python -m nodo_api.admin migrate 2>&1 \
  | grep -E "migrations applied" || { echo "FAIL: migrations did not apply"; exit 1; }

echo "== start the API"
docker run -d --name "$NAME" --network "$NETWORK" "${env_args[@]}" "$IMAGE" >/dev/null
for _ in $(seq 1 30); do
  if docker exec -i "$NAME" python - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2)
PY
  then break; fi
  sleep 1
done

echo "== probes"
docker exec -i "$NAME" python - <<'PY'
import json, sys, urllib.error, urllib.request

base = "http://127.0.0.1:8000"
problems = []


def get(path):
    try:
        response = urllib.request.urlopen(base + path, timeout=10)
        return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


status, headers, _ = get("/healthz")
if status != 200:
    problems.append(f"/healthz answered {status}")
for header in ("x-content-type-options", "strict-transport-security", "x-request-id"):
    if header not in {k.lower() for k in headers}:
        problems.append(f"header {header} is missing")
status, _, _ = get("/readyz")
if status != 200:
    problems.append(f"/readyz answered {status} (the database check)")
status, _, body = get("/v1/meetings")
if status != 401 or json.loads(body).get("error", {}).get("code") != "unauthorized":
    problems.append(f"/v1/meetings without a token answered {status}")
for problem in problems:
    print("FAIL:", problem)
print("probes: ok" if not problems else "probes: failed")
sys.exit(1 if problems else 0)
PY

echo "== worker processes"
workers=$(docker exec -i "$NAME" python - <<'PY'
import os

count = 0
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        command = open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        continue
    if b"spawn_main" in command and b"tracker_fd" in command and b"pipe_handle" in command:
        count += 1
print(count)
PY
)
# uvicorn spawns one process per worker (and Python adds a resource tracker): two or more
[ "${workers:-0}" -ge 2 ] || { echo "FAIL: expected 2 worker processes, found ${workers:-0}"; exit 1; }
echo "worker processes: $workers"
echo "OK"
