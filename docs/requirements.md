# MVP Requirements

## Product Goal

Expense Agent is a Telegram-first expense logger. A user sends natural-language messages to a Telegram bot, the backend turns supported messages into structured expense records, stores them in Google Sheets, and replies with a clear confirmation or correction prompt.

The MVP optimizes for reliable manual expense capture, correction, and lightweight spending lookup. It is not an autonomous finance agent.

## Supported User Stories

### Record an Expense

As a user, I can send a natural-language expense message so the bot records it without forcing me into a form.

Examples:

- `lunch 12.50`
- `grab to office 18.20 交通`
- `昨天超市 43.80 购物`
- `coffee SGD 5.60 with Alex`

Expected behavior:

- Private text messages are supported directly; group and supergroup messages are supported only when they explicitly mention the bot username.
- The bot extracts amount, currency, description, category, and transaction date when present.
- The bot applies configured defaults for missing optional fields.
- The bot validates required fields before writing to storage.
- The bot replies with the saved transaction summary.

### Clarify Invalid or Ambiguous Input

As a user, I can receive a useful prompt when a message cannot safely become a transaction.

Expected behavior:

- Missing amount, unsupported currency, invalid date, or unclear intent does not write to Google Sheets.
- Ambiguous parse results produce a clarification reply that names the field needing correction.
- Unsupported requests receive a concise explanation of what the MVP supports.

### Update a Recent Transaction

As a user, I can correct a recent transaction through Telegram when I notice a mistake.

Examples:

- `change last lunch to 13.20`
- `刚才那笔改成交通`
- `改成 cny`
- `不是 SGD，是 CNY`
- `把昨天咖啡改成餐饮`
- `改一下，我吃了白鸡饭花了6.8`

Expected behavior:

- The backend resolves the target transaction before applying the update.
- The bot does not update storage when the target transaction is missing or ambiguous.
- The bot applies valid inferred changes such as amount, date, currency, category, merchant, note, and payment method.
- Extra parser fields that are not supported for updates do not block otherwise valid safe changes.
- The bot replies with the updated transaction summary.

### Query Stored Spending

As a user, I can ask simple questions about previously recorded spending.

Examples:

- `how much did I spend today?`
- `这个月花了多少？`
- `5月10日到20日花了多少？`

Expected behavior:

- Queries read from Google Sheets without writing new transaction rows.
- Querying, aggregation, exchange-rate conversion, and reply formatting must not
  append or update any Google Sheets row or cell.
- A new row may be appended only after a create-expense request passes parsing,
  validation, and duplicate checks.
- The implemented query supports an inclusive date range and returns a total
  with category breakdowns; category-filtered and recent-expense list queries
  remain future work.
- Legacy current-month parser responses end on the requesting message's local
  date rather than including future-dated rows.

## MVP Defaults

- Default timezone: configured deployment timezone, initially `Asia/Singapore`.
- Default currency: configured user currency, initially `SGD`.
- Default transaction date: the Telegram message date in the configured timezone.
- Default category: `未分类` when a valid expense is clear but no supported category is confidently present.
- Generated storage timestamps use the configured timezone and include an explicit offset.

## Supported Currencies

The app stores the original expense amount and currency. Missing currency
defaults to `SGD`; explicit supported currencies are preserved and used for
reporting conversion only.

Supported mainstream currencies:

- `SGD`
- `CNY`
- `USD`
- `EUR`
- `GBP`
- `JPY`
- `HKD`
- `TWD`
- `MYR`
- `IDR`
- `THB`
- `VND`
- `KRW`
- `AUD`
- `NZD`
- `CAD`
- `CHF`
- `INR`
- `PHP`

Expense confirmations preserve the original foreign-currency amount and show
its SGD equivalent using the transaction-date reference rate. Spending queries
accept an inclusive date range, convert every foreign-currency row to SGD using
its transaction-date rate, and report the SGD total, original foreign-currency
subtotals, actual rate dates used, and SGD category totals with percentages.

## Supported Categories

The MVP category set is curated and stable so parsing, storage, and reporting stay consistent:

- `餐饮`
- `交通`
- `购物`
- `住房`
- `订阅`
- `娱乐`
- `医疗`
- `教育`
- `办公`
- `旅行`
- `个人护理`
- `生活服务`
- `家庭`
- `服饰`
- `数码`
- `健身`
- `礼物`
- `税费`
- `保险`
- `其他`
- `未分类`

Parser output must normalize synonyms into these values. For example, `mrt`,
`taxi`, and `grab` map to `交通`; `doctor` and `medicine` map to `医疗`;
`剪头发` maps to `个人护理`.

## Out of Scope

- Receipt OCR or image parsing.
- Bank, card, wallet, or settlement-rate integrations.
- Budgeting, alerts, recurring transactions, or forecasting.
- Shared ledgers, bill splitting, reimbursements, or group accounting.
- Live trading exchange rates or bank/card settlement-rate matching.
- Income, investment, asset, liability, or tax tracking.
- Direct user editing inside Google Sheets as an application workflow.
- Autonomous agent loops, background financial advice, or tool use by the LLM.
- Deployment automation beyond what future implementation issues explicitly add.

## Acceptance Cases

### Expense Creation

- Given a message with a positive amount and expense description, when the bot handles it, then one transaction and its creation event are committed in PostgreSQL before confirmation.
- Given a message without an explicit currency, when the transaction is valid, then the configured default currency is stored.
- Given a message without an explicit date, when the transaction is valid, then the Telegram message date is stored in the configured timezone.
- Given a message with a supported category synonym, when the parser returns a transaction, then the stored category uses the normalized category value.
- Given the same Telegram message update is processed more than once, when a transaction already exists for that Telegram message, then the backend does not create a duplicate row.

### Validation and Clarification

- Given a message with no amount, when the bot handles it, then no row is written and the reply asks for the amount.
- Given a message with a zero or negative expense amount, when the bot validates it, then no row is written and the reply explains that expenses must be positive.
- Given a message with an unsupported currency, when the bot validates it, then no row is written and the reply asks for a supported ISO 4217 currency.
- Given parser confidence is below the implementation threshold, when the bot handles the message, then no row is written and the reply asks the user to rephrase or provide the missing field.

### Updates

- Given an update request identifies one stored transaction, when the requested field value is valid, then the existing transaction is updated instead of appending a new row.
- Given an update request contains valid safe changes plus unsupported parser fields, when the bot handles it, then the safe changes are applied and unsupported fields are ignored.
- Given an update request identifies no transaction, when the bot handles it, then no row is changed and the reply asks which transaction to update.
- Given an update request matches multiple recent transactions, when the bot handles it, then no row is changed and the reply asks the user to clarify.

### Queries

- Given a supported total-spend query, when matching transactions exist, then the bot replies with the SGD total, original foreign-currency subtotals, and category amounts and percentages in SGD.
- Given a foreign-currency conversion uses a previous available rate, when the
  bot replies, then the actual rate date is visible.
- Given a legacy current-month parser response, when the bot reads matching
  transactions, then the inclusive end date is the requesting message's local
  date.
- Given a query has no matching transactions, when the bot handles it, then the bot replies with an empty-result message and does not write to storage.

## Future MVP Issue Breakdown

Future issues should map each acceptance case to a red test or explicit manual check before implementation:

- Category-filtered spending queries and recent-expense list queries.
- Parser contract: supported intents, required fields, confidence, and category normalization.
- Domain validation: transaction invariants, update invariants, query invariants, and idempotency.
- Google Sheets repository: append, update, lookup, and query behavior against a fake or test sheet.
- Telegram adapter: update decoding, message metadata capture, reply formatting, and duplicate delivery behavior.
- Application service orchestration: command routing, validation before storage, storage before confirmation, and error replies.
- Configuration and deployment baseline: timezone, currency, credentials, and environment validation.
