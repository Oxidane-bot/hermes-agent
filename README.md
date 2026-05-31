<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent — Oxidane Fork

This repository is a personal operational fork of upstream
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent). It
keeps the parts needed for the live local Hermes profiles, records why those
changes exist, and follows upstream only when an update is useful enough to
forward-port.

For upstream product documentation, installation instructions, and generic
feature marketing, use the upstream repository and docs. This README only
summarizes what is special about this fork.

## What this fork is for

- Run and maintain the local `default` and `kurisu` Hermes profiles.
- Keep operational fixes deployed without waiting for upstream to accept or
  rediscover them.
- Make fork-only behavior easy to audit before future upstream updates.
- Keep the fork branch list small instead of mirroring old upstream PR/topic
  branches.

## Maintained feature set

| Feature | User-facing value | Representative commits |
|---|---|---|
| Goal automation | `/goal` survives session compression and stops on evidence of completion instead of looping on bare “done” text. The judge timeout is configurable, so the stronger main model can be used reliably. | `6f98baee4`, `b0b6a411` |
| Voice message handling | Voice notes are delivered to the model as marked transcripts, not exact user wording. The model is prompted to treat ASR text as fallible and use conversation context; voice replies can also answer pending clarification prompts. | `2ab1f2c91`, `d813a03d` |
| Provider fast modes | `/fast` keeps Anthropic `speed=fast` separate from ChatGPT/Codex priority processing, while older `service_tier=fast` configs still behave as expected. This prevents one provider's “fast” switch from accidentally changing another provider's routing semantics. | `a8b81172b` |
| Long-session compaction | Compression produces structured handoff checkpoints that preserve the active task, current work state, files, tests, and remaining work. The request shape also keeps the system-prefix/prompt-cache behavior consistent instead of drifting after compaction. | `e2e898f60`, `60a518780`, `39880e662` |
| Codex Responses replay | Auxiliary compaction follows the same Responses-style replay shape as the main agent, preserving tool calls, function outputs, reasoning items, and timeouts/fallbacks instead of flattening the conversation into lossy text. | `60a518780`, `39880e662` |
| Attachment delivery reliability | When the agent references local files or `MEDIA:` paths, the gateway attempts native upload for supported files, including archives. If an attachment cannot be parsed or delivered, the user gets an explicit warning and lifecycle hooks see the turn as failed. | `27843b92d` |
| Web search credentials | Tavily can use a pool of API keys with fill-first selection and cooldown, keeping web search available when one key hits quota without wasting quota on background probes. | `06abd9d49` |
| Background review safety | Background review can collect memory and propose skills, but skill creation or mutation requires approval instead of happening silently from a non-blocking review thread. | `dcd0d249` |

## Branch model

- `origin/main` — active maintained fork line and deployment source.
- `upstream/main` — fetched from `NousResearch/hermes-agent` only when comparing
  or forward-porting upstream changes; it is not mirrored into `origin`.
- Additional local-maintained branches should stay local unless they have an
  active documented purpose that is not already represented on `main`.
- Backup branches, temporary PR branches, and old upstream topic branches are
  not intended steady-state fork branches.

See [`docs/local-changes/branch-strategy.md`](docs/local-changes/branch-strategy.md)
for the exact policy.

## Where the fork deltas are documented

- [`docs/local-changes/README.md`](docs/local-changes/README.md) — maintained
  index of local fork changes.
- [`docs/local-changes/local-branch-2026-05.md`](docs/local-changes/local-branch-2026-05.md)
  — commit-level notes, user impact, verification, and forward-port cautions.
- [`docs/local-changes/branch-strategy.md`](docs/local-changes/branch-strategy.md)
  — remotes, branch cleanup policy, and upstream intake flow.

## Commit landmarks

| Commit | Purpose |
|---|---|
| `b776f462` | Clarifies the fork branch contract and upstream intake policy. |
| `b0b6a411` | Requires evidence before `/goal` judge completion and honors judge timeout config. |
| `d813a03d` | Treats voice transcripts as fallible model context. |
| `a8b81172b` | Separates Anthropic Fast Mode from ChatGPT/Codex priority processing. |
| `dcd0d249` | Requires approval before background review creates skills. |
| `27843b92d` | Makes attachment delivery failures visible. |
| `39880e662` | Keeps compaction and Responses replay compatible with the current upstream runtime. |
| `06abd9d49` | Keeps Tavily usable across pooled keys. |
| `6f98baee4` | Preserves goals across compression session splits. |
| `60a518780` | Keeps compaction robust through real Codex Responses replay. |
| `e2e898f60` | Adds structured handoff checkpoints for long-session compaction. |

## Updating from upstream

Use upstream as an input, not as the public branch surface of this fork:

```bash
git fetch upstream --tags
```

Review `upstream/main` against `origin/main`, forward-port only the changes that
are worth carrying, verify locally, and document any new fork-only behavior under
`docs/local-changes/`.
