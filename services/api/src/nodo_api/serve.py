"""Process entry point for the API container.

Replaces a bare `uvicorn` command line because the settings that matter for a service behind a
gateway are easy to get wrong there:

- `WEB_CONCURRENCY` worker processes (one per vCPU: Python executes one thread at a time per
  process, so a replica with two cores and one worker uses one);
- keep-alive longer than the gateway's idle timeout, or the gateway reuses a connection the API
  has just closed and the user sees a 502;
- a graceful shutdown window shorter than the platform's termination grace period, so in-flight
  requests finish when a replica is replaced;
- proxy headers trusted only from the networks in FORWARDED_ALLOW_IPS (the gateway subnet);
- no server or date header, and no access log (the application logs each request itself, as JSON).
"""

from __future__ import annotations

import os

import uvicorn

KEEP_ALIVE_SECONDS = 75  # above the 60 s idle timeout of the common gateways and load balancers
GRACEFUL_SECONDS = 25  # below the 30 s the platform gives a replica to stop
WS_MAX_BYTES = 64 * 1024  # a voice frame is at most 32 KB: nobody may send 16 MB before hello


def main() -> None:
    uvicorn.run(
        "nodo_api.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=int(os.environ.get("WEB_CONCURRENCY", "2")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=GRACEFUL_SECONDS,
        ws_max_size=WS_MAX_BYTES,
        server_header=False,
        date_header=False,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
