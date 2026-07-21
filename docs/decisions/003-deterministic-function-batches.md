# ADR 003: Deterministic One-Shot Function Batches

Status: Accepted

## Context

The parser-oriented agent could recognize a small set of intents, but broader
statistics questions depended too heavily on model interpretation and reply
generation. This made financial answers difficult to verify and allowed model
output to blur the boundary between request understanding, calculation, ledger
access, and user-visible wording.

The product must support multiple operations in one message, preserve exact
provider-delivery idempotency, treat intentional repeated messages as separate
entries, and keep PostgreSQL as the sole authoritative ledger.

## Decision

The LLM runs once per new inbound message and returns one complete, ordered,
non-empty batch of allowlisted function calls. It receives no operation results,
has no ledger credentials, does not calculate totals, and produces no final
user-visible text.

The backend validates the complete batch before mutation. All write calls commit
atomically in PostgreSQL; read calls run after that commit and can observe the
new state. A read failure never rolls back committed writes. Backend functions
own date resolution, filtering, currency conversion, aggregation, ranking, and
deterministic reply rendering.

The exact provider platform/chat/message identity is the delivery idempotency
key. A retry returns the stored deterministic reply. A different provider
message ID executes normally even when its content is identical. A minimal
structured pending request may retain one clarification per identity and chat
for ten minutes; it is not conversation history.

The target selection model is the OpenAI Responses API model `gpt-5.5` with
strict function schemas and required tool choice. Model proposals remain
untrusted inputs to backend validation.

## Consequences

- Financial state and calculations are reproducible from PostgreSQL and backend
  code rather than model prose.
- One message can create multiple expenses or combine writes with statistics.
- Delete and bulk-destructive functions are absent from the exposed catalog.
- Batch, call-index, result, and reply persistence add schema and recovery state.
- The legacy parser path remains available during integration; production
  exposure requires explicit validation and approval. The owner approved direct
  production validation without staging for the 2026-07-21 release.
