# Testing Strategy

## TDD Workflow

Every implementation issue should follow Red, Green, Refactor, and Verify unless the issue explicitly justifies an exception.

### Red

Before changing behavior, add the smallest failing test or check that captures the acceptance criterion.

Examples:

- A parser contract test for a new supported natural-language pattern.
- A domain validation unit test for invalid amount or unsupported category.
- An application service test proving no Google Sheets write occurs on clarification.
- A repository contract test proving duplicate Telegram updates do not append duplicate rows.
- A documentation check proving required docs exist before this baseline was added.

### Green

Add the smallest implementation that makes the red test pass while staying inside issue scope.

Implementation guidance:

- Prefer local domain logic before provider integration.
- Keep LLM prompts behind the parser port.
- Keep Telegram and Google Sheets clients behind adapters or repositories.
- Preserve existing behavior unless the issue explicitly changes it.

### Refactor

After tests pass, remove duplication and clarify names without broadening behavior.

Refactor guidance:

- Add an abstraction after three concrete uses or when a boundary is already defined in architecture.
- Keep parser, validation, orchestration, and storage responsibilities separate.
- Delete dead code and obsolete docs in the same slice when they are directly affected.

### Verify

Run the repo verification command and any manual checks named in the issue.

For this documentation baseline, the verification command is:

```sh
bash -lc 'status=0; for f in docs/requirements.md docs/domain-model.md docs/architecture.md docs/testing-strategy.md; do if ! test -s "$f"; then printf "%s missing or empty\n" "$f"; status=1; fi; done; exit "$status"'
```

Future code issues should replace or extend this with the project test command once the test runner exists.

## Expected Test Types

### Domain Unit Tests

Cover deterministic business rules:

- Amount must be positive.
- Currency must be supported and normalized.
- Category must be a canonical category.
- Transaction date must be valid and timezone-aware at the boundary.
- Update requests must resolve exactly one transaction before mutation.
- Queries must not mutate storage.

### Parser Contract Tests

Cover expected parser behavior without giving the parser authority over execution:

- Natural-language create transaction examples.
- Category synonym normalization.
- Missing required fields.
- Unsupported intent.
- Update request extraction.
- Query request extraction.
- Confidence thresholds and clarification paths.

Parser tests may use fixtures, mocked LLM responses, or deterministic parser implementations depending on the issue. They should assert the structured parser result, not provider-specific wording.

### Application Service Tests

Cover orchestration:

- Valid create request parses, validates, appends, and confirms.
- Invalid create request does not append and asks for clarification.
- Duplicate Telegram update does not append a second row.
- Valid update request resolves one row, updates it, and confirms.
- Ambiguous update request does not mutate storage.
- Query request reads rows and formats the expected reply.

### Repository Contract Tests

Cover Google Sheets persistence behavior behind a repository interface:

- Append maps every transaction field to the documented column.
- Lookup by Telegram metadata supports idempotency.
- Update changes only allowed fields and preserves source metadata.
- Query filters by user or chat policy, date range, and category.

Repository tests should prefer a fake repository for application service tests and a narrow integration check for the real Google Sheets client when credentials are available.

### Telegram Adapter Tests

Cover adapter behavior:

- Telegram update payload becomes message metadata plus text.
- Reply calls target the source chat.
- Non-message updates are ignored or rejected according to the implementation issue.
- Telegram API errors are surfaced without storage mutation.

## Documentation Checks

Documentation issues should include a lightweight check that fails when required files are missing or empty. Content quality still requires manual review against the issue acceptance criteria.

Manual review checklist for this baseline:

- `docs/requirements.md` identifies supported user stories, out-of-scope features, defaults, categories, acceptance cases, and future MVP issue breakdown.
- `docs/domain-model.md` defines transaction fields, Telegram metadata, parser results, update requests, query requests, supported categories, storage row shape, and validation invariants.
- `docs/architecture.md` states the IM adapters are interfaces, backend services own orchestration, the LLM is parser-only, PostgreSQL is authoritative, and Google Sheets is a projection.
- `docs/testing-strategy.md` explains Red, Green, Refactor, Verify, expected test types, and the current docs verification command.

## Issue-to-Test Mapping

Each future MVP issue should include:

- The user-visible behavior being changed.
- The domain or architecture boundary touched.
- The first failing test or check to add.
- The smallest implementation expected to pass it.
- The verification command.
- Any manual smoke check that cannot yet be automated.

Recommended issue slices:

- Parser contract and category normalization.
- Transaction domain validation.
- Google Sheets repository.
- Telegram adapter.
- Create transaction application flow.
- Update transaction application flow.
- Query transaction application flow.
- Configuration and runtime startup validation.
