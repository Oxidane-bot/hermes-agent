# Local Hermes Agent maintenance branch

This file is the working note for the active local patch stack.
For the maintained index and branch policy, see
`docs/local-changes/README.md`, `docs/local-changes/local-branch-2026-05.md`,
and `docs/local-changes/branch-strategy.md`.

## Base

- Upstream project: NousResearch/hermes-agent
- Active fork remote: Oxidane-bot/hermes-agent
- Branch purpose: keep local reliability patches reviewable and testable before deployment.

## Current local stack

- `fix: inherit compression cache and reasoning settings`
  - Preserves compression-related runtime settings across agent state changes.
- `fix: prefer primary-provider model fallbacks`
  - Allows `model.fallback_models` to try another model on the same configured provider before provider-level failover.
  - Keeps provider names and endpoints config-driven.
- `fix: harden Telegram command and document delivery`
  - Registers the Telegram command menu after the adapter is connected so menu updates do not block polling startup.
  - Uses the Telegram Bot API command limit and logs hidden commands when the menu is full.
  - Rejects oversized document uploads with a clear failure instead of silently sending a local file path as text.
- `fix: support env file attachments`
  - Allows `MEDIA:` tags, bare local paths, and inbound Telegram documents to treat `.env`, `.env.*`, and `*.env.*` files as text attachments.
  - Keeps secret file contents out of docs and tests; examples use redacted placeholder values only.

## Verification and launch

- Targeted test command for the attachment work:
  - `python -m pytest -o 'addopts=' tests/gateway/test_platform_base.py tests/gateway/test_telegram_documents.py -q`
- Local startup smoke for this fork:
  - `./.venv/bin/hermes status`

## Privacy notes

- Do not commit profile configs, `.env`, auth files, sessions, logs, or request dumps.
- Keep custom provider endpoints and provider-specific slugs out of public documentation and source comments.
- Describe fallback behavior generically as same-provider model fallback followed by provider-level failover.

## License

Hermes Agent is MIT licensed. This fork preserves upstream license attribution.
