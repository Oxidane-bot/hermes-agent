# Local Changes — v0.17 Port with Restored Features

This branch contains hermes-agent v0.17 with restored custom features from the v0.15 fork.

## Base

- **Upstream:** NousResearch/hermes-agent v0.17
- **Fork remote:** Oxidane-bot/hermes-agent
- **Branch:** `main` (promoted from `rebase/v0.17-port` on 2026-06-25)
- **Legacy:** `main-v0.15-legacy-backup` (archived v0.15 custom branch)

## Restored Features (6 total)

During the v0.17 port, 98 custom commits were squashed to 2, losing 9 features. Six have been restored:

1. **Primary Provider Model Fallbacks** (`d90fdb23d`, `c20869394`, `279cfbb61`)
   - Prefers same-provider model fallbacks before cross-provider failover
   - Fixes cron auth fallback model routing
   - Documents cron fallback provider support

2. **Preserve Goals Across Compression** (`631e3a406`)
   - Maintains task continuity through automatic session splits
   - Prevents goal loss during context compaction

3. **Approval Reviewer Fallback Chain** (`63c588bdf`)
   - Uses configured fallback chain when primary approval reviewer fails
   - Improves approval workflow robustness

4. **Telegram Working Bubble Deduplication** (`541a0bc6e`)
   - Prevents duplicate status indicators during transient edit failures
   - Improves Telegram UX

5. **Stale Stream Failover** (`449687de7`)
   - Detects unresponsive streaming providers (60s chunk timeout)
   - Auto-fails over to next provider
   - Prevents indefinite hangs

6. **Memory Read-Before-Write Guard** (`1c5d42b82`)
   - Requires `memory(action='read', target=X)` before add/replace/remove
   - Conservative review prompts encourage maintenance-only behavior
   - Prevents blind overwrites

**See [docs/local-changes/restored-features.md](docs/local-changes/restored-features.md) for full details.**

## Skipped Features (3 total)

Three features evaluated but not restored:

1. **fd/fdfind file search fallback** — Already present in v0.17 port
2. **Telegram topic compression rebind** — v0.17 has superior `_sync_telegram_topic_binding`
3. **Provider-level browser headers** — Depends on deleted AI Gateway provider

## Testing

All restored features verified against baseline test suite:
- **Baseline:** ≥666 passed, 2 skipped
- **New tests:** 20+ assertions for stale stream failover and memory read-before-write

Run full suite:
```bash
uv sync --extra dev
uv run pytest -q
```

## Development

**Prerequisites:**
- Python 3.11+
- uv (package/environment manager)

**Common commands:**
```bash
uv sync --extra dev          # Install/update dependencies
uv run pytest -v             # Verbose test run
uv run pytest tests/tools/   # Specific module
```

**Commit conventions:**
- Use original commit message format when restoring features
- Include `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer
- Preserve Oxidane-bot authorship: `git -c user.name="Oxidane-bot" -c user.email="oxidane-bot@users.noreply.github.com" commit`

## Privacy

- Do not commit profile configs, `.env`, auth files, sessions, logs, or request dumps
- Keep custom provider endpoints out of public documentation
- Describe fallback behavior generically

## License

Hermes Agent is MIT licensed. This fork preserves upstream license attribution.
