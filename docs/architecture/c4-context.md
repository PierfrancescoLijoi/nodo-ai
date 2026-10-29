# C4 level 1 — system context

Status: target architecture, 24 October 2026. Solid relationships are in the current product or
Terraform; the real-time voice relationship is planned and remains behind an implementation gate.

```mermaid
C4Context
  title NODO AI — system context
  Person(member, "Workspace member", "Records meetings and asks questions")
  Person(operator, "Platform operator", "Deploys, observes and recovers the service")
  System(nodo, "NODO AI", "Captures meetings, transcribes speech and provides ACL-aware meeting intelligence")
  System_Ext(identity, "Microsoft Entra External ID", "Authenticates members and issues access tokens")
  System_Ext(azureAi, "Azure AI services", "Speech recognition, embeddings and language models")
  System_Ext(observability, "Azure Monitor", "Telemetry, SLOs, alerts and operational evidence")

  Rel(member, identity, "Signs in with", "OIDC PKCE")
  Rel(member, nodo, "Uploads recordings and asks questions", "HTTPS")
  Rel(member, nodo, "Streams audio and receives live results [target]", "WSS")
  Rel(nodo, azureAi, "Transcribes, embeds and generates answers", "Private Azure endpoints")
  Rel(operator, nodo, "Deploys and operates")
  Rel(nodo, observability, "Exports traces, metrics and logs", "OpenTelemetry")
  Rel(operator, observability, "Responds to SLO and capacity alerts")
```

The trust boundary is NODO AI. Azure AI never authorizes a workspace request: tenancy, ACLs,
admission and idempotency stay inside NODO.
