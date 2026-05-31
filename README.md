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

## Fork-specific behavior

| Area | What changed here |
|---|---|
| `/goal` loop | Goal state survives compression/session splits; the judge now requires concrete completion evidence instead of accepting bare “done” text; judge timeout is configurable for slower main-model verdicts. |
| Voice messages | Platform voice notes are transcribed before the model sees them; the prompt now marks transcripts as fallible so the model can use context to infer intended meaning. Voice replies can also satisfy pending clarification prompts. |
| Context compression | Checkpoint-framed v2 compaction is retained, with Codex Responses replay/recovery fixes and compatibility updates for upstream runtime changes. |
| Messaging gateway | Attachment delivery failures are surfaced instead of hidden behind successful text delivery. Session/topic persistence fixes keep Telegram goal and compression flows coherent. |
| Web/search tooling | Tavily multi-key pooling, cooldown, crawl auth, and merged provider documentation are kept as local operational improvements. |
| Review safety | Background review can propose skills, but it cannot silently create or mutate the skills surface without approval. |
| Fork maintenance | README, branch strategy, and local-change docs identify this as a needs-driven personal fork, not an upstream PR staging area. |

## Branch model

- `origin/main` — active maintained fork line and deployment source.
- `origin/upstream-main` — mirror of upstream `main`, used only for comparison
  and forward-port review.
- Additional local-maintained branches should exist only while they have an
  active documented purpose. The current checkpoint worktree is
  `impl/v2-compaction-checkpoint`.
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
| `e58803e6b` | Clarifies the fork branch contract and upstream comparison branch. |
| `abaa238fd` | Requires evidence before `/goal` judge completion and honors judge timeout config. |
| `5fc9603ae` | Treats voice transcripts as fallible model context. |
| `1c4de266c` | Requires approval before background review creates skills. |
| `4cab4b729` | Makes attachment delivery failures visible. |
| `9bb825941` | Keeps local checks aligned with current upstream runtime semantics. |
| `e7c742a0c` | Keeps Tavily usable across pooled keys. |
| `6f98baee4` | Preserves goals across compression session splits. |
| `60a518780` | Keeps compaction robust through real Codex Responses replay. |
| `e2e898f60` | Retains checkpoint-framed v2 context compaction. |

## Updating from upstream

Use upstream as an input, not as the public branch surface of this fork:

```bash
git fetch upstream --tags
git push --force-with-lease origin upstream/main:refs/heads/upstream-main
```

Review the upstream diff against `origin/main`, forward-port only the changes
that are worth carrying, verify locally, and document any new fork-only behavior
under `docs/local-changes/`.
