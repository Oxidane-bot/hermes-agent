# Oxidane local Hermes Agent fork

This repository is a personal public fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

It carries local workflow patches that are useful for my Hermes setup. It is not an official Nous Research distribution.

## Current patch stack

Base: `v2026.4.30`

### Branch `local-v2026.4.23` commits

| Commit | Description |
|--------|-------------|
| `201bc262b` | file search fallback behavior when `rg` is unavailable |
| `e4844f899` | local-command STT language auto-detection |
| `85d8a4f80` | auxiliary connection-error classification |
| `f52581c18` | conservative memory read-before-write behavior |
| `a4666cdc4` | stale streaming-provider failover |
| `94b708577` | context-compression fail-closed behavior |
| `ffea243f2` | local `uv.lock` refresh |

### Branch `fix/smart-approval-fallback` additional commits

| Commit | Description |
|--------|-------------|
| `3c3d51d09` | file search fd fallback when `rg` is unavailable |
| `6fa29bc07` | memory read-before-write behavior |
| `d64b0368e` | stale streaming-provider failover |
| `254e23937` | context-compression fail-closed behavior |
| `bd5f627a2` | rebind telegram topics after compression |
| `497de707a` | approval reviewer fallback chain |
| `32b94c7e4` | auxiliary compaction replay real Responses turns |
| `aa4f1d524` | expand native file attachment extensions |
| `a3bd27c43` | provider-level request headers (config + auxiliary client) |
| `1f3703dff` | provider-level browser/attribution headers in agent runtime |
| `93d9056d2` | unify fallback chain (`fallback_models` + `fallback_providers`) and gateway clarify replies |

### Uncommitted

- independent Anthropic fast-mode and Codex priority configurations (live code only, not yet in fork)
- Telegram proxy rotation helper: `gateway/telegram_proxy_rotation.py` (Clash/Mihomo selector switching via `127.0.0.1:9090`) + `gateway/telegram_rotation_client.py` (fire-and-forget failure event reporting) + hooks in `gateway/platforms/telegram.py` (polling network error, heartbeat probe failure)

## Upstream sync model

- `upstream`: `NousResearch/hermes-agent`
- `origin`: `Oxidane-bot/hermes-agent`
- local branches: `local-v2026.4.23`, `fix/smart-approval-fallback`

When upstream releases a new version, update by fetching upstream and rebasing or merging the local patch stack onto the target release/tag, then run the Hermes test suite.

MIT license and original copyright notices are preserved.
