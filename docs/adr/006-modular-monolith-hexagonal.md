# ADR-006: Modular monolith with hexagonal ports and adapters

Status: accepted

## Decision
The API is one deployable with modules (identity, capture, compliance, knowledge, retrieval, platform) that communicate through typing.Protocol ports and events. Adapters (memory, Azure) are swappable by configuration. Workers reuse the same domain package.

## Consequences
- Fast local development with in-memory adapters.
- A module can become a service when a split trigger is hit (different scaling, security zone or release cadence).
