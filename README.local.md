# Local Hermes Agent maintenance branch

This branch is Oxidane's personal maintenance branch for a public fork of NousResearch/hermes-agent. It is not an official Hermes Agent distribution.

## Base

- Upstream project: NousResearch/hermes-agent
- Active fork remote: Oxidane-bot/hermes-agent
- Branch purpose: keep small local reliability patches reviewable and testable before deployment.

## Patch stack

- `fix: inherit compression cache and reasoning settings`
  - Preserves compression-related runtime settings across agent state changes.
- `fix: prefer primary-provider model fallbacks`
  - Allows `model.fallback_models` to try another model on the same configured provider before provider-level failover.
  - Keeps provider names and endpoints entirely config-driven.
- `fix: harden Telegram command and document delivery`
  - Registers the Telegram command menu after the adapter is connected so menu updates do not block polling startup.
  - Uses the Telegram Bot API command limit and logs hidden commands when the menu is full.
  - Rejects oversized document uploads with a clear failure instead of silently sending a local file path as text.

## Test notes

Run targeted tests with:

```bash
python -m pytest -o 'addopts=' tests/cli/test_cli_init.py tests/run_agent/test_provider_fallback.py tests/gateway/test_session_model_override_routing.py tests/cron/test_scheduler.py tests/gateway/test_telegram_documents.py -q
```

Use `-o 'addopts='` when the local test environment lacks optional pytest plugins referenced by repository defaults.

## Privacy notes

- Do not commit profile configs, `.env`, auth files, sessions, logs, or request dumps.
- Keep custom provider endpoints and provider-specific slugs out of public documentation and source comments.
- Describe fallback behavior generically as same-provider model fallback followed by provider-level failover.

## License

Hermes Agent is MIT licensed. This fork preserves upstream license attribution.
