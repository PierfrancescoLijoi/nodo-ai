# ADR-003: Hybrid search plus graph traversal, ACL before retrieval

Status: accepted

## Decision
Answers combine hybrid retrieval (BM25 plus dense plus rerank) with bounded graph lookups. Workspace and ACL filters are applied inside every store query, never after fetching. LangGraph is limited to the planner and tool loop; policy is deterministic code.

## Consequences
- Cross-workspace leakage is prevented structurally and tested with negative cases.
- Graph hops, nodes, tokens and time are capped per request.
