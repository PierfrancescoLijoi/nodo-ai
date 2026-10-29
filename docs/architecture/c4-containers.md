# C4 level 2 — production containers

Status: target architecture, 24 October 2026. Containers marked **target** are specified but not yet
implemented in the application or Terraform. Everything else has code and infrastructure definitions,
although no Azure subscription deployment has yet validated them.

```mermaid
C4Container
  title NODO AI — production containers
  Person(member, "Workspace member", "Android or desktop client")

  System_Boundary(nodo, "NODO AI") {
    Container(edge, "Edge and API gateway", "Front Door Premium + APIM", "WAF, token/rate policy and regional routing")
    Container(api, "API", "FastAPI on Container Apps", "Authorization, meeting lifecycle, retrieval and admission")
    Container(voice, "Voice Gateway [target]", "ASGI on Container Apps", "Binary audio streaming, Speech connection, replay and backpressure")
    Container(worker, "Worker jobs", "Container Apps Jobs", "Idempotent batch transcription, indexing and analysis")
    ContainerQueue(bus, "Work queue", "Service Bus Premium", "Durable asynchronous commands")
    ContainerDb(store, "Source of truth", "PostgreSQL + Blob", "Workspace data, transcript and governed audio")
    ContainerDb(search, "Retrieval index", "Azure AI Search", "ACL-filtered hybrid retrieval; rebuildable")
    ContainerDb(session, "Session state [target]", "Azure Managed Redis", "Leases, acknowledgements, routing and quota counters; no raw audio")
  }

  System_Ext(identity, "Microsoft Entra External ID", "Identity provider")
  System_Ext(speech, "Azure Speech", "Batch and streaming speech recognition")
  System_Ext(models, "Azure OpenAI", "Embeddings and streamed answers")

  Rel(member, identity, "Authenticates", "OIDC PKCE")
  Rel(member, edge, "REST and WSS handshake", "HTTPS")
  Rel(edge, api, "Routes REST", "Internal HTTPS")
  Rel(edge, voice, "Routes dedicated WSS path [target]", "Internal WSS")
  Rel(api, bus, "Publishes idempotent work")
  Rel(api, store, "Reads and writes governed data")
  Rel(api, search, "Retrieves authorized evidence")
  Rel(worker, bus, "Consumes one command per execution")
  Rel(worker, speech, "Submits and polls batch transcription")
  Rel(worker, store, "Persists final results")
  Rel(worker, search, "Indexes derived content")
  Rel(voice, session, "Coordinates resumable sessions [target]")
  Rel(voice, speech, "Streams audio and receives partial/final text [target]")
  Rel(voice, bus, "Publishes finalized segments only [target]")
  Rel(api, models, "Embeds and answers through APIM AI gateway")
```

The Voice Gateway is deliberately not part of the API process: a slow model, indexing task or REST
burst must not block audio receive loops. Redis is coordination state, never the durable transcript.
