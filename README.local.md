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
| `a2eafd945` | file search fallback (rebased onto newer upstream) |
| `e77ac1ab2` | memory read-before-write (rebased) |
| `8e2e116b0` | stale streaming-provider failover (rebased) |
| `7d51bf35f` | context-compression fail-closed (rebased) |
| `c931c2131` | rebind telegram topics after compression |
| `ac1fdc913` | approval reviewer fallback chain |
| `b5253c827` | auxiliary compaction replay real Responses turns |

### Uncommitted

- independent Anthropic fast-mode and Codex priority configurations (live code only, not yet in fork)

## Upstream sync model

- `upstream`: `NousResearch/hermes-agent`
- `origin`: `Oxidane-bot/hermes-agent`
- local branches: `local-v2026.4.23`, `fix/smart-approval-fallback`

When upstream releases a new version, update by fetching upstream and rebasing or merging the local patch stack onto the target release/tag, then run the Hermes test suite.

MIT license and original copyright notices are preserved.
