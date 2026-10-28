# ADR-004: Temporal claims with evidence and human approval on ambiguity

Status: accepted

## Decision
Relations carry validity time, `SUPPORTED_BY` evidence and extraction version. LLM output stays `proposed` until a person approves it. Two people are never merged on a shared first name.

## Consequences
- Decision history (SUPERSEDES) is queryable and auditable.
- Review UI is a first-class feature, not an afterthought.
