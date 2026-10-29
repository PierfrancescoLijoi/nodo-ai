# ADR-028: A streaming voice gateway that can lose a replica without losing a phrase

Status: accepted (implements the build order of docs/VOICE-REALTIME-ARCHITECTURE.md, steps 1 to 4)

## Context
The architecture note scored the real-time path at 3/10 because the streaming gateway, the shared
session state, speech synthesis and reconnect existed only as a design. The requirements that
shaped the code: a person can drop off Wi-Fi, a replica can be rolled out in the middle of a
meeting, and neither may lose or duplicate a phrase; the assistant must be as grounded as the
written one; and nothing may depend on a clock the replicas do not share.

## Decision
- **Audio is addressed by offset, not by counter.** A frame is a four byte offset in milliseconds
  and PCM16. After a reconnect the client resends from the offset the server announces; the server
  drops what it already has and trims a frame that overlaps. Duplicates and gaps are counted, never
  fatal.
- **A final is stored before it is announced.** The gateway writes a finalized phrase to the
  session store, advances the acknowledged offset, and only then sends it. Once a client has seen a
  final it may discard that audio, so the phrase has to survive the replica first.
- **Any replica can finish a session.** The store holds the finals (idempotent per start offset,
  expiring after six hours), the acknowledged offset and the last sequence. A replica that takes a
  session over starts its recognizer at the acknowledged offset; the one that ends the session
  reads the finals from the store, so it does not matter which replica saw which phrase.
- **A drain discards what was half heard.** Stopping a replica flushes nothing it did not
  finalize: the client resends from the last stored final, so the next replica hears the same
  audio once and no phrase is cut in two.
- **One lease per session, with the store's own clock.** A replica owns a session while it renews
  a lease (2.5 heartbeats); a crashed replica is replaced when the lease lapses, a drained one
  releases it at once. Lua scripts keep each step atomic and Redis's `TIME` is the only clock.
- **The ticket is the first message.** The session is opened with the normal authenticated REST
  call, which returns a one-use ticket bound to a session and a person (HMAC, about a minute).
  Browsers cannot send headers on a WebSocket and a token in the address is logged, so the ticket
  is presented as `hello`. The gateway checks the person and the meeting again at that point.
- **Reliable and ephemeral events.** Events that matter carry a sequence, an id derived from it and
  stay in a bounded replay log until acknowledged; partial transcripts carry neither and are never
  replayed. A client too slow for the bounded send queue is cut off and recovers from the log.
- **The assistant reuses the answer path.** A spoken question goes through `QueryService` under
  the same gate as the written one, so access control, evidence validation and citations are the
  same; the answer is cut into sentences and spoken one at a time. A barge-in cancels the turn,
  stops the synthesizer and drops audio already queued for the socket.
- **A live meeting ends as a normal one.** The finals become transcript version 1, the meeting
  moves to TRANSCRIBING and the usual job is published; the worker finds the transcript and goes on
  with indexing and extraction. Every step is safe to repeat.
- **Providers are ports.** `StreamingRecognizer` and `SpeechSynthesizer` are implemented for Azure
  Speech (diarized transcription for meetings, neural voices in four languages), and a buffered
  recognizer reuses the local Whisper for push to talk. A scripted recognizer drives the tests and
  the load run.

## Alternatives considered
- **Frame counters instead of offsets.** Simpler, but a counter says nothing about audio a new
  replica never heard; offsets make the resume the same operation everywhere.
- **Finalize on drain.** Keeps more text, but cuts phrases at the replica boundary and needs a
  merge afterwards. Resending up to one phrase is cheaper and exact.
- **A sticky load balancer instead of shared state.** Works until a replica is replaced, which is
  exactly the case to survive.
- **Persisting finals in PostgreSQL as they arrive.** Cleaner for retention, but needs tables and
  an incremental transcript model the pipeline does not have; Redis holds them for hours and the
  pipeline receives them once, complete. Revisit if live meetings must survive a Redis failure.

## Consequences
- New settings under `NODO_VOICE_*`; the gateway is off unless `NODO_VOICE_ENABLED` is set, and
  refuses to start without a ticket key (and, on Azure, a Redis URL).
- The API image gains `redis` and the Speech SDK; the SDK's native objects were constructed inside
  the slim production image to prove it loads there.
- The Azure resources are in Terraform (`infra/terraform/modules/nodo/voice*.tf`), behind
  `voice_enabled` (default off, so existing plans do not change); they have been planned with mocked
  providers and never applied. Redis (TLS only, `noeviction`, private endpoint in the professional
  tier) and a second Container App per stamp from the API image (two replicas minimum, a connection
  scale rule, a termination grace period of 25 s + drain + 15 s) carry the gateway. The voice app
  runs as the API identity plus the Speech user role; a dedicated identity needs a database role
  and edits to `main.tf`/`rbac.tf`. Front Door can not reach the internal load balancer without a
  private link approval, so API Management exposes `/v1/voice/stream` as a WebSocket API and
  `/v1/voice/sessions` as a REST API, both behind the Front-Door-only check; the existing `/v1/*`
  route at the edge already carries them, so it has no rule of its own. Front Door's two hour and
  five minute idle limits end a socket the gateway would keep: the heartbeat (15 s) holds it open
  and the client's resume covers the two hour cut. To be proven on a subscription: APIM
  `set-backend-service` with a `wss://` base URL, path precedence over the public API, Redis
  Basic/Standard/Premium being on the retirement path (Managed Redis is the successor).
- Not covered by this change, and listed in the architecture note: a capture client, VAD and echo
  cancellation, token-streamed answers, alerts on the voice app (`monitoring.tf`). Nothing here has
  run against Azure Speech.
- The numbers in the architecture note come from a scripted provider on one machine: they bound
  the gateway's own cost and say nothing about the providers' latency.
