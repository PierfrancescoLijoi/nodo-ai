# ADR-023: Confidence on first connection comes from checks that run, not from a longer document

Status: accepted. The checks are implemented and tested against fakes and, for the pieces that touch PostgreSQL, a real database; the container is exercised in CI against a real PostgreSQL. Everything that talks to Azure itself runs for the first time on the first deployment, which is what the checks are for.

## Context
The Azure side of NODO AI was designed, written and tested without an Azure subscription. Its adapters were checked against fakes and documented request shapes; its infrastructure against `terraform validate` and plan-time assertions with mocked providers. That removes a great deal, and none of it can prove that the real services accept what the code sends or that the real network lets the parts talk. A longer design document does not change that. What does is a short, repeatable way to find out, on the day, which of the assumptions holds, and a way to turn the ones that fail into a setting rather than a rewrite.

## Decision

### What was proved offline (and stays proved: every one of these runs in CI)
- The application behaves the same on memory, SQLite and PostgreSQL where it has to (shared contract suites); migrations 1 to 9 apply to an empty database and the roles they create can do what they should and nothing more (tests on a real PostgreSQL, as the role).
- **The real container image**, in Azure mode, against a real PostgreSQL: every migration applies, the app starts under the production process settings with two workers, `/healthz` and `/readyz` answer, security headers are set, an unauthenticated call is refused with the standard error body (`tools/verify/container-check.sh`).
- The strict JSON schemas sent to the models obey the structured-output rules (all properties required, no additional properties, no unsupported keywords).
- The API Management policies render, parse as XML, use only elements of the policy language, read only named values Terraform defines, and keep expressions that contain quotes in single-quoted attributes.
- Terraform: syntax and provider schema (`validate`); composition and the properties that must not regress per tier (`terraform test` with mocked providers); and the limits Azure enforces on names (Container Apps names stop at 32 characters, which this found).
- The Azure adapters' request shapes against the documented ones, with fake transports.

### What the first-connection checks prove (the new part)
`python -m nodo_api.admin preflight api|worker`, a manual job per region and identity. Each check uses the code production uses, leaves nothing behind, and prints what is probably wrong and where to look when it fails:

| Check | Proves |
|---|---|
| identity | a token can be obtained for PostgreSQL, Storage, Search, Cognitive Services and Service Bus |
| postgres_connect | the Entra login works and is the identity we expect (so `pgaadauth_create_principal` worked) |
| postgres_schema | every migration is applied; the `vector` and `pg_trgm` extensions exist |
| postgres_isolation | every table that holds workspace data forces row level security; a stranger's workspace sees nothing |
| blob_roundtrip | the write link and read link the apps are given actually work; a request without a link is refused (anonymous access is off); the test object is removed |
| servicebus_send / receive | the identity may send (a message scheduled an hour out and cancelled: nothing is ever delivered) / receive (peek) |
| search_index | the index has the fields, the vector dimension and the four language analyzers the code expects |
| embedding | the embedding model returns vectors of the configured size |
| search_roundtrip | a document is written, counted, found by a hybrid query and removed |
| model_answer / model_extraction | the chat deployment accepts the *strict* answer schema and the *strict* extraction schema, and the answer is grounded |
| speech | the Speech endpoint answers the transcription API with this identity's token |
| gateway | the public host answers `/readyz` through Front Door and API Management |

And `tools/smoke/smoke.py`: the whole product path through the public door (record, upload by blob link, process, transcript, question, graph, export, delete). With generated silence the right result is a failed meeting, which proves upload, queue, worker, Speech and the failure path with no recording; with real speech the right result is `review`.

### Turning assumptions into settings
The few things that could not be known offline each got a switch, so the likely failure costs a variable, not a change of design: `secrets_from_key_vault` (Container Apps resolving Key Vault references behind a firewall), `ai_token_limit_enabled` (API Management accepting the token-budget policy on a hand-made model API). FIRST-DEPLOY.md lists the others and what each failure looks like.

### The order
FIRST-DEPLOY.md: the basic tier first (few moving parts, cheap, proves the application against real services), then the professional tier on a throwaway subscription, then the recovery rehearsals. Each phase ends in a gate; a red gate has a row in the troubleshooting table.

## Alternatives considered
- **Emulators** (Azurite, the Service Bus emulator). Azurite does not support user delegation SAS, which the whole upload path uses; the Service Bus emulator does not model Entra authentication or private endpoints. They would test an imitation of the thing we are unsure about.
- **Recorded API responses (VCR style).** Recorded from what? Nothing has run yet. After the first deployment the preflight output is a better record than a cassette.
- **Test the infrastructure by deploying it on every pull request.** Right for a team with a subscription; it is the next step once there is one, and the preflight and smoke jobs are the post-deploy gates such a pipeline would call.

## Consequences
- A pass of every check does not prove capacity, long-run behaviour or the failover; those are the load test and the rehearsals of phase 4.
- The checks create and remove objects under a workspace named `preflight`; an interrupted run can leave one such object, which the weekly index check would report as a leftover and the retention lifecycle would remove.
- The checks need the same configuration as the app (they read it the same way), so a misspelled setting fails the check first, with a clearer message than a failing app.
