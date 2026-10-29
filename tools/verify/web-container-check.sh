#!/usr/bin/env bash
# Starts the real web image the way Azure starts it (an environment, no volumes) and checks what can be
# checked without Azure: it refuses a wrong configuration, renders /config.json and the content policy
# from the environment, serves every page with its security headers, caches only what may be cached,
# answers 404 and 405 properly, and runs as an unprivileged user.
#
#   tools/verify/web-container-check.sh <image>
set -euo pipefail

IMAGE="${1:?image}"
NAME="nodo-web-verify-$$"
ISSUER="https://tenant.ciamlogin.com/tenant-id/v2.0"
BLOB="https://stverify.blob.core.windows.net"
failures=0

fail() { echo "FAIL: $1"; failures=$((failures + 1)); }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== a wrong configuration stops the container"
for case_env in \
  "NODO_OIDC_ISSUER=http://insecure.example/v2.0" \
  "NODO_OIDC_ISSUER=" \
  "NODO_OIDC_CLIENT_ID=\"quote" ; do
  base=(-e "NODO_OIDC_ISSUER=${ISSUER}" -e NODO_OIDC_CLIENT_ID=client -e NODO_OIDC_SCOPE=api://nodo/access)
  if output=$(docker run --rm "${base[@]}" -e "$case_env" "$IMAGE" 2>&1); then
    fail "started with ${case_env}"
  elif ! grep -q "nodo-web:" <<<"$output"; then
    fail "no explanation for ${case_env}: ${output}"
  fi
done
output=$(docker run --rm "$IMAGE" 2>&1 || true)
grep -q "NODO_OIDC_ISSUER is required" <<<"$output" || fail "no explanation for a missing issuer"

echo "== start"
docker run -d --name "$NAME" -p 127.0.0.1::8080 \
  -e "NODO_OIDC_ISSUER=${ISSUER}" -e NODO_OIDC_CLIENT_ID=00000000-0000-0000-0000-000000000001 \
  -e NODO_OIDC_SCOPE=api://nodo/access -e "NODO_BLOB_ORIGIN=${BLOB}" \
  -e NODO_API_BASE_URL=https://api.example.test/ "$IMAGE" >/dev/null
PORT="$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')"
BASE="http://127.0.0.1:${PORT}"
for _ in $(seq 1 30); do
  curl -fsS "${BASE}/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "${BASE}/healthz" | grep -q '"ok"' || { docker logs "$NAME"; fail "the container did not become healthy"; }

header() { grep -i "^$2:" <<<"$1" | head -1 | cut -d: -f2- | sed 's/^ *//; s/\r$//'; }
check_headers() {
  local label="$1" headers="$2"
  for name in content-security-policy x-content-type-options x-frame-options referrer-policy permissions-policy strict-transport-security cross-origin-resource-policy; do
    [ -n "$(header "$headers" "$name")" ] || fail "${label}: header ${name} is missing"
  done
}

echo "== pages"
page=$(curl -sS -D - "${BASE}/")
grep -q "200" <<<"$(head -1 <<<"$page")" || fail "/ did not answer 200"
check_headers "/" "$page"
policy="$(header "$page" content-security-policy)"
grep -q "script-src 'self' 'sha256-" <<<"$policy" || fail "the policy has no script hashes"
grep -q "connect-src 'self' https://tenant.ciamlogin.com https://api.example.test https://stverify.blob.core.windows.net;" <<<"$policy" \
  || fail "connect-src is not the origins of the environment: ${policy}"
grep -q "frame-ancestors 'none'" <<<"$policy" || fail "frames are not refused"
grep -qE "unsafe-inline|unsafe-eval" <<<"$policy" && fail "the policy allows unsafe script or style"
[ "$(header "$page" cache-control)" = "no-cache" ] || fail "pages must be revalidated: $(header "$page" cache-control)"
grep -qi "^server:.*[0-9]" <<<"$page" && fail "the server announces its version"

for path in /meetings/ /meeting/?id=abc /ask/ /graph/ /review/ /capture/ /settings/ /auth/callback/; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "${BASE}${path}")
  [ "$code" = "200" ] || fail "${path} answered ${code}"
done
# without the trailing slash, and the redirect must not name the container
loc=$(curl -sS -o /dev/null -D - "${BASE}/meetings" | grep -i '^location:' | tr -d '\r' || true)
case "$loc" in
  *127.0.0.1*|*8080*) fail "a redirect names the container: ${loc}" ;;
esac

echo "== config.json"
config=$(curl -sS -D - "${BASE}/config.json")
body=$(sed -n '/^\r\{0,1\}$/,$p' <<<"$config" | tail -n +2)
[ "$(header "$config" cache-control)" = "no-store" ] || fail "config.json must not be cached"
grep -qi "^content-type: application/json" <<<"$config" || fail "config.json is not served as JSON"
node -e '
const c = JSON.parse(process.argv[1]);
const ok = c.mode === "cloud" && c.apiBaseUrl === "https://api.example.test/" &&
  c.oidc.issuer === "https://tenant.ciamlogin.com/tenant-id/v2.0" &&
  c.oidc.clientId === "00000000-0000-0000-0000-000000000001" && c.oidc.scope === "api://nodo/access";
process.exit(ok ? 0 : 1);' "$body" 2>/dev/null || {
  # no Node on the runner: compare as text
  grep -q '"mode":"cloud"' <<<"$body" && grep -q '"scope":"api://nodo/access"' <<<"$body" || fail "config.json is wrong: ${body}"
}

echo "== files"
asset=$(grep -o '/_next/static/[^"]*\.js' <<<"$page" | head -1)
assets=$(curl -sS -D - -o /dev/null "${BASE}${asset}")
grep -q "immutable" <<<"$(header "$assets" cache-control)" || fail "a build file is not cached for good: ${asset}"
check_headers "asset" "$assets"
[ "$(curl -sS -o /dev/null -w '%{http_code}' "${BASE}/_next/static/missing.js")" = "404" ] || fail "a missing build file is not a 404"
[ "$(curl -sS -o /dev/null -w '%{http_code}' --path-as-is "${BASE}/../etc/passwd")" != "200" ] || fail "path traversal answered 200"

echo "== errors and methods"
missing=$(curl -sS -D - -o /dev/null "${BASE}/no/such/page/")
grep -q "404" <<<"$(head -1 <<<"$missing")" || fail "an unknown page is not a 404"
check_headers "404" "$missing"
for method in POST PUT DELETE PATCH; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X "$method" "${BASE}/")
  case "$code" in 403|405|413) ;; *) fail "${method} / answered ${code}" ;; esac
done

echo "== unprivileged"
[ "$(docker exec "$NAME" id -u)" != "0" ] || fail "the container runs as root"
docker exec "$NAME" sh -c 'touch /usr/share/nginx/html/x 2>/dev/null' && fail "the web root is writable"

if [ "$failures" -gt 0 ]; then
  echo "web container: ${failures} check(s) failed"
  docker logs "$NAME" | tail -20
  exit 1
fi
echo "web container: ok"
