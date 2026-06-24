# May 2026 Local Fork Changes

Original baseline: `fork/local` at `eb76cdd740dc4dcf07542eb7c217b642d03b763b`.
Current local head when this document was created: `27815ffb8`.

Forward-port check baseline: upstream release `v2026.5.29.2` (`77a1650c7`),
checked on branch `upgrade/v2026.5.29.2-check`.

This file documents the local-only commits above the fork baseline so future maintenance can tell which behavior is intentional and which can be dropped when upstream catches up. It is linked from the top-level README as the maintained inventory of fork-specific behavior.

## Forward-port status against `v2026.5.29.2`

When updating from the old local branch to upstream `v2026.5.29.2`, do **not**
replay `ee12c69f7 chore: sync local fork to May 2026 snapshot`. That commit was
a historical upstream snapshot sync, not a local customization. Replaying it on
top of the latest release would drag many files back toward an older upstream
state.

Instead, use the latest release as the base and forward-port only the local
behavioral deltas below.

| Original commit | Forward-port status on `upgrade/v2026.5.29.2-check` | Notes |
|---|---|---|
| `eb76cdd74` | Ported | Checkpoint-framed v2 compaction retained. One upstream test expectation was merged with the local checkpoint prompt shape. |
| `b8825c787` | Ported with refactor adaptation | `run_agent.py` init changes moved to upstream's `agent/agent_init.py`; auxiliary Responses replay retained. |
| `54cd78206` | Ported | Voice replies can satisfy pending clarify prompts while preserving upstream's audio-file attachment handling. |
| `f1431f9db` | Not replayed | Superseded by upstream/new local `agent.codex_runtime` event-stream handling; old `responses.stream().get_final_response()` assertions are obsolete. |
| `9b6f33272` | Not replayed | Superseded by upstream `codex_runtime` raw event consumption and null-output regression coverage. |
| `c8f0de31b` | Not replayed | Same stream-recovery cluster as above; replaying would reintroduce old stream helper paths. |
| `eb9c7e9cc` | Ported | Anthropic fast mode remains separate from service-tier priority. |
| `6351d442a` | Ported | `/goal` migration across compression session splits retained and combined with upstream Telegram topic rebinding/session-store save behavior. |
| `27815ffb8` | Ported with docs merge | Tavily multi-key/cooldown/crawl auth retained; web-search docs merged with upstream's newer Brave/DDGS/xAI provider rows. |
| `20df23368` | Ported | This documentation directory retained. |

Additional branch-only compatibility and maintenance commits:

- `39880e662` updates tests and minor compatibility around upstream's current
  runtime semantics: `ContextCompressor._generate_summary()` now receives
  `protected_head`, and xAI Responses replay keeps encrypted reasoning per
  upstream `agent.codex_responses_adapter` behavior.
- `e5c238dd9` keeps Codex response tests hermetic by mocking live metadata and
  pricing lookups that the latest release may otherwise perform during unit
  tests.
- `b8d34d80` documents the upstream `v2026.5.29.2` forward-port boundary so
  future updates know which old local patches were replayed, superseded, or
  intentionally left behind.
- `27843b92d` makes required attachment delivery failures visible instead of
  treating text delivery as overall success when file uploads fail.
- `dcd0d249` requires explicit approval before background review can create
  skills, keeping non-blocking review threads from mutating the skills surface
  without operator consent.
- `5045483d` makes the repository identity explicit as a personal fork, adds
  branch strategy guidance, and preserves upstream release/tag intake through
  the `upstream` remote.
- `9ed60b33` links the top-level README to this maintained local-change index
  so the fork identity and local delta inventory are visible from the default
  project entry point.
- `d813a03d` treats voice transcripts as fallible context, so model prompts
  know transcribed voice may contain recognition errors and should use nearby
  context when inferring intent.
- `b0b6a411` requires concrete evidence before the `/goal` judge marks a goal
  complete and makes the judge timeout configurable for slower main-model
  verdicts.
