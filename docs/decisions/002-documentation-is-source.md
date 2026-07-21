# ADR 002: Documentation Is the System Source

Status: Accepted

## Context

Expense Agent is maintained by people and software Agents across short-lived
working contexts. Code alone can show executable behavior but does not reliably
preserve product intent, boundary ownership, rejected alternatives, rollout
state, or the reason a constraint exists. Unowned or duplicated documentation
also drifts and becomes unsafe to trust.

## Decision

The repository handbook is the normative system definition. Requirements own
observable intent, interfaces own boundary contracts, architecture owns current
responsibilities and data flow, and accepted ADRs own durable decisions. Code
and tests are executable conformance evidence: they compile the documented
model into behavior and demonstrate where that behavior currently conforms.

The [handbook index](../index.md) assigns one owner to each fact category and
defines conflict handling. A discrepancy between documentation and executable
behavior is recorded in [Current State](../now.md) and resolved explicitly; it
is not silently treated as proof that either artifact should overwrite the
other. Changes update the owning documents, implementation, and relevant tests
in the same branch and pull request.

## Consequences

- Repository entry points must route readers to the handbook before deep code
  exploration.
- Behavioral, interface, persistence, or architectural changes are incomplete
  until their owning documents and conformance evidence agree.
- `now.md` remains a concise handoff and does not become a changelog.
- Accepted decisions are superseded with new ADRs rather than rewritten.
- Documentation tests protect required structure and local links, while review
  still checks semantic accuracy.
- Generated specifications may support the handbook but cannot replace the
  human-readable owner of a boundary or decision.
