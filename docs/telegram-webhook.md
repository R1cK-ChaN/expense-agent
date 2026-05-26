# Telegram Webhook

## Runtime Configuration

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in the service
environment before handling messages. Set `TELEGRAM_BOT_USERNAME` without the
leading `@` when the bot should process explicit group or supergroup mentions.
The health endpoint still starts without external credentials, but webhook
payloads need the secret for request verification and handled text or non-text
payloads need the bot token so the service can send Telegram replies.

## Route

Telegram should send updates to:

```text
POST /telegram/webhook
```

Current MVP behavior:

- Private text messages are normalized for orchestration and receive the handler
  reply.
- Private non-text messages receive the unsupported-message reply.
- Group and supergroup text messages are processed only when the text explicitly
  mentions `@TELEGRAM_BOT_USERNAME`; the mention is stripped before parser input.
- Group and supergroup messages without the bot mention are acknowledged with
  HTTP 200 and ignored without a reply.

## Manual Smoke Test

Run the service locally:

```sh
uvicorn app.main:app --reload
```

With `TELEGRAM_BOT_TOKEN` configured, post a private text-shaped payload:

```sh
curl -sS -X POST http://127.0.0.1:8000/telegram/webhook \
  -H 'Content-Type: application/json' \
  -H "X-Telegram-Bot-Api-Secret-Token: $TELEGRAM_WEBHOOK_SECRET" \
  -d '{
    "update_id": 1000,
    "message": {
      "message_id": 9001,
      "date": 1779278400,
      "chat": {"id": 12345, "type": "private"},
      "from": {"id": 42, "is_bot": false, "first_name": "Ada"},
      "text": "lunch 12.30"
    }
  }'
```

For a real bot webhook, expose the service over HTTPS and register the URL with
Telegram's `setWebhook` method using the same value as the `secret_token`.
Do not commit or paste the bot token or webhook secret into repo files.
