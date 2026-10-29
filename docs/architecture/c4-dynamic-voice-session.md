# C4 dynamic — resilient live transcription session

Status: target interaction, 24 October 2026. This is the acceptance contract for the Voice Gateway,
not a description of code that already exists.

```mermaid
C4Dynamic
  title NODO AI — live transcription, disconnect and resume
  Person(member, "Workspace member", "Android or desktop client")
  Container(api, "Session API", "Authorizes meeting and capacity")
  Container(voiceA, "Voice Gateway A", "Receives binary audio")
  Container(voiceB, "Voice Gateway B", "Replacement after reconnect")
  ContainerDb(session, "Managed Redis", "Lease, sequence and acknowledgement cursor")
  System_Ext(speech, "Azure Speech", "Continuous recognition")
  ContainerQueue(bus, "Service Bus", "Finalized segment events")

  RelIndex(1, member, api, "Requests a short-lived, meeting-bound session ticket")
  RelIndex(2, api, session, "Reserves tenant and provider capacity")
  RelIndex(3, member, voiceA, "Opens WSS and sends numbered binary audio frames")
  RelIndex(4, voiceA, speech, "Writes continuous audio")
  RelIndex(5, speech, voiceA, "Returns partial and final recognition events")
  RelIndex(6, voiceA, member, "Publishes ordered transcript events")
  RelIndex(7, voiceA, bus, "Publishes final segments with idempotency keys")
  RelIndex(8, member, voiceB, "Reconnects with session id and highest contiguous acknowledgement")
  RelIndex(9, voiceB, session, "Claims lease and reads cursor")
  RelIndex(10, member, voiceB, "Replays only the bounded missing audio window")
  RelIndex(11, voiceB, bus, "Deduplicates and continues final segment publication")
```

Required invariants: an event id is durable and idempotent; partial text is replaceable; final text is
never indexed twice; active sessions survive admission closure; a reconnect outside the grace window
starts a new recognition epoch instead of pretending continuity.
