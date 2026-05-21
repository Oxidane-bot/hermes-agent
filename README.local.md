# Oxidane local Hermes Agent fork

This repository is a personal public fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

It carries local workflow patches that are useful for my Hermes setup. It is not an official Nous Research distribution.

## Current patch stack

Base: `v2026.4.30`

Local commits currently cover:

- file search fallback behavior when `rg` is unavailable
- local-command STT language auto-detection
- auxiliary connection-error classification
- conservative memory read-before-write behavior
- stale streaming-provider failover
- context-compression fail-closed behavior
- local `uv.lock` refresh

## Upstream sync model

- `upstream`: `NousResearch/hermes-agent`
- local branch: `local-v2026.4.23`

When upstream releases a new version, update by fetching upstream and rebasing or merging the local patch stack onto the target release/tag, then run the Hermes test suite.

MIT license and original copyright notices are preserved.
