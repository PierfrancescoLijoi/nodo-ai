# ADR-005: Separate user attestation, legal basis and operator duties

Status: accepted

## Decision
Each meeting stores a server-signed attestation by the user. It records a declaration, not participant consent, and it does not transfer the operator's obligations. Terms acceptance, privacy notice and attestation are three distinct records. Texts are versioned per locale.

## Consequences
- Legal review is a release gate.
- A participant notice or invite flow can be added without changing the record shape.
