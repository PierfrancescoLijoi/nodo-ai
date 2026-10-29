# C4 deployment — professional tier

Status: target deployment, 24 October 2026. The current Terraform implements the async resources.
Voice Gateway and Managed Redis are gated target nodes and must not be presented as deployed.

```mermaid
C4Deployment
  title NODO AI — professional Azure deployment
  Deployment_Node(edge, "Global edge", "Azure Front Door Premium") {
    Container(frontdoor, "Public edge", "WAF and health-based regional routing")
  }
  Deployment_Node(primary, "Primary Azure region", "Availability zones") {
    Deployment_Node(computeP, "Private Container Apps environment", "Zone redundant") {
      Container(apiP, "API replicas", "Minimum three")
      Container(workerP, "Event-driven worker jobs", "Service Bus scaler")
      Container(voiceP, "Voice Gateway replicas [target]", "Minimum two; connection-aware scaling")
    }
    Deployment_Node(dataP, "Private data plane", "Private endpoints") {
      ContainerDb(pgP, "PostgreSQL", "Zone-redundant HA")
      ContainerDb(busP, "Service Bus Premium", "Primary namespace")
      ContainerDb(redisP, "Managed Redis [target]", "HA and zone redundant")
    }
  }
  Deployment_Node(standby, "Standby Azure region", "Warm data, cold compute") {
    Container(apiS, "API", "Scaled to zero until failover")
    ContainerDb(pgS, "PostgreSQL replica", "Cross-region replica")
    ContainerDb(busS, "Service Bus namespace", "Geo-DR alias target")
    ContainerDb(redisS, "Managed Redis [target]", "Active geo-replica for coordination only")
  }
  Deployment_Node(ai, "EU Azure AI data zone", "Quota-governed services") {
    Container(speech, "Speech resources", "Batch and streaming quotas")
    Container(models, "OpenAI deployments", "Primary and alternate region")
  }

  Rel(frontdoor, apiP, "Routes healthy REST traffic")
  Rel(frontdoor, apiS, "Fails over after activation")
  Rel(frontdoor, voiceP, "Routes WSS handshake [target]")
  Rel(apiP, pgP, "Reads and writes")
  Rel(apiP, busP, "Publishes work")
  Rel(workerP, busP, "Consumes work")
  Rel(workerP, speech, "Batch transcription")
  Rel(apiP, models, "Retrieval generation")
  Rel(voiceP, speech, "Continuous recognition [target]")
  Rel(voiceP, redisP, "Lease and replay cursor [target]")
  Rel(pgP, pgS, "Replicates")
  Rel(busP, busS, "Aliases metadata; sweep repairs lost in-flight work")
  Rel(redisP, redisS, "Eventually replicates disposable session state [target]")
```

Admission uses the lowest available capacity across gateway sessions, approved Speech concurrency,
model quota and Front Door connections. The standby is not called active-active: database promotion
and an operator failover lever are required.
