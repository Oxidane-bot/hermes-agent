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

- `9bb825941` updates tests and minor compatibility around upstream's current
  runtime semantics: `ContextCompressor._generate_summary()` now receives
  `protected_head`, and xAI Responses replay keeps encrypted reasoning per
  upstream `agent.codex_responses_adapter` behavior.
- `74a0085ab` keeps Codex response tests hermetic by mocking live metadata and
  pricing lookups that the latest release may otherwise perform during unit
  tests.
- `4e5e46b8a` documents the upstream `v2026.5.29.2` forward-port boundary so
  future updates know which old local patches were replayed, superseded, or
  intentionally left behind.
- `4cab4b729` makes required attachment delivery failures visible instead of
  treating text delivery as overall success when file uploads fail.
- `1c4de266c` requires explicit approval before background review can create
  skills, keeping non-blocking review threads from mutating the skills surface
  without operator consent.
- `fe67c67b9` makes the repository identity explicit as a personal fork, adds
  branch strategy guidance, and preserves upstream release/tag intake through
  the `upstream` remote.
- `484e9b681` links the top-level README to this maintained local-change index
  so the fork identity and local delta inventory are visible from the default
  project entry point.
- `5fc9603ae` treats voice transcripts as fallible context, so model prompts
  know transcribed voice may contain recognition errors and should use nearby
  context when inferring intent.
- `abaa238fd` requires concrete evidence before the `/goal` judge marks a goal
  complete and makes the judge timeout configurable for slower main-model
  verdicts.

Verification for the forward-port branch:

- `scripts/run_tests.sh tests/agent/test_context_compressor.py tests/agent/test_compress_focus.py tests/agent/test_context_compressor_summary_continuity.py tests/agent/test_auxiliary_client.py tests/run_agent/test_run_agent_codex_responses.py tests/cli/test_fast_command.py tests/gateway/test_fast_command.py tests/hermes_cli/test_goals.py tests/tools/test_web_tools_tavily.py tests/tools/test_web_tools_config.py tests/hermes_cli/test_config.py`
  — 606 tests passed.
- `scripts/run_tests.sh tests/gateway/test_compression_session_id_persistence.py tests/gateway/test_goal_status_notice.py tests/gateway/test_session_hygiene.py tests/gateway/test_voice_command.py tests/test_cli_manual_compress.py tests/cli/test_cli_skin_integration.py tests/providers/test_e2e_wiring.py`
  — 226 tests passed.
- `git diff --check` and `python3 -m py_compile` on modified Python surfaces
  passed. `cli.py` still emits an existing `return in finally` SyntaxWarning.

Operational caveat: no live gateway smoke was run because using the real
`~/.hermes` gateway config can interfere with currently running production
adapters, tokens, webhooks, sessions, and outbound message delivery.

## Summary table

| Commit | Area | User-visible? | Documentation status |
|---|---|---:|---|
| `b8825c787` | Context compression / Responses replay | Indirect | Recorded here |
| `54cd78206` | Messaging voice clarify replies | Yes | Recorded here |
| `f1431f9db` | Auxiliary compaction null output recovery | Indirect | Recorded here |
| `9b6f33272` | Codex Responses stream recovery | Indirect | Recorded here |
| `c8f0de31b` | Stream-first Codex output recovery | Indirect | Recorded here |
| `eb9c7e9cc` | `/fast` mode / Anthropic priority | Yes | Recorded here |
| `6351d442a` | Persistent `/goal` across compression splits | Yes | Recorded here |
| `27815ffb8` | Tavily multi-key pool | Yes | Product docs updated + recorded here |
| `4cab4b729` | Gateway attachment delivery failure reporting | Yes | Recorded here |
| `1c4de266c` | Background-review skill proposal approval | Yes | Recorded here |
| `fe67c67b9` | Fork identity and branch strategy | Developer-facing | README + branch strategy docs |
| `484e9b681` | Local-change index discoverability | Developer-facing | README + recorded here |
| `5fc9603ae` | Voice transcript prompt semantics | Yes | Recorded here |
| `abaa238fd` | `/goal` judge completion evidence | Yes | Recorded here |

## Context compression and Codex Responses recovery

### `b8825c787` — Keep compaction robust through real Responses replay

Intent: keep context compression reliable when real OpenAI/Codex Responses replay data differs from the idealized final output shape.

Touched areas:

- `agent/auxiliary_client.py`
- `agent/context_compressor.py`
- `run_agent.py`
- `tests/agent/test_auxiliary_client.py`
- `tests/agent/test_context_compressor.py`

Behavioral note: this is mostly an internal reliability fix. Users see fewer failed compactions and fewer false provider-outage diagnoses.

Verification recorded in commit:

- `venv/bin/python -m pytest tests/agent/test_auxiliary_client.py tests/agent/test_context_compressor.py tests/tools/test_clarify_gateway.py -q`

Maintenance notes:

- Keep auxiliary Codex compact conversion aligned with `agent.codex_responses_adapter`.
- Preserve system prompt prefix behavior because prompt-cache parity depends on it.
- Live PRIMARY_PROVIDER compaction was not tested at commit time.

### `f1431f9db` — Recover auxiliary compact from null Responses output

Intent: recover compaction output from streamed evidence when the final Responses snapshot contains null or malformed completed output.

Touched areas:

- `agent/auxiliary_client.py`
- `tests/agent/test_auxiliary_client.py`

Behavioral note: internal reliability fix. It prevents local compaction aborts when PRIMARY_PROVIDER is otherwise healthy.

Verification recorded in commit:

- `venv/bin/python -m pytest tests/agent/test_auxiliary_client.py::TestCodexAuxiliaryAdapterTimeout -q`
- `venv/bin/python -m pytest tests/agent/test_auxiliary_client.py tests/agent/test_context_compressor.py -q`
- `venv/bin/python -m py_compile agent/auxiliary_client.py tests/agent/test_auxiliary_client.py`

Maintenance notes:

- Prefer streamed evidence over a malformed final snapshot.
- Live PRIMARY_PROVIDER compaction after restart was not tested at commit time.

### `9b6f33272` — Recover Codex streams from null completed output

Intent: keep main Codex streaming turns usable when completed output parsing fails on null output even though streamed text/items were already received.

Touched areas:

- `run_agent.py`
- `tests/run_agent/test_run_agent_codex_responses.py`

Behavioral note: users may see successful responses instead of failures after a malformed final Responses object.

Verification recorded in commit:

- `venv/bin/python -m pytest tests/agent/test_auxiliary_client.py::TestCodexAuxiliaryAdapterTimeout tests/run_agent/test_run_agent_codex_responses.py::test_run_codex_stream_recovers_from_null_completed_output_text_delta tests/run_agent/test_run_agent_codex_responses.py::test_run_codex_stream_recovers_from_null_completed_output_item -q`
- `venv/bin/python -m pytest tests/run_agent/test_run_agent_codex_responses.py tests/run_agent/test_streaming.py::TestCodexStreamCallbacks tests/agent/test_auxiliary_client.py tests/agent/test_context_compressor.py -q`
- `venv/bin/python -m py_compile run_agent.py agent/auxiliary_client.py tests/run_agent/test_run_agent_codex_responses.py tests/agent/test_auxiliary_client.py`

Maintenance notes:

- Do not treat malformed final snapshots as provider outages when streamed content is recoverable.
- Live PRIMARY_PROVIDER request after restart was not tested at commit time.

### `c8f0de31b` — Recover streamed Codex output before final snapshots

Intent: make stream recovery happen before final snapshot processing so already-received output is preserved even if the final response object is incomplete.

Touched areas:

- `agent/auxiliary_client.py`
- `run_agent.py`
- `tests/agent/test_auxiliary_client.py`
- `tests/run_agent/test_run_agent_codex_responses.py`

Behavioral note: continuation of the stream recovery fixes above; users should see fewer blank/failed Codex turns after streaming produced usable content.

Maintenance notes:

- This overlaps conceptually with `f1431f9db` and `9b6f33272`; review all three together before forward-porting or deleting any one of them.

## Messaging clarify fixes

### `54cd78206` — Let voice replies satisfy pending clarify prompts

Intent: allow a voice transcript to resolve an outstanding `clarify` prompt the same way a normal text reply can.

Touched area:

- `gateway/run.py`

User-visible behavior:

- If Hermes is waiting for a clarification answer, a user can answer by voice instead of being forced to type.
- Voice transcript handling must happen before normal media/document dispatch so the pending prompt is consumed correctly.

Verification recorded in commit:

- `venv/bin/python -m pytest tests/agent/test_auxiliary_client.py tests/agent/test_context_compressor.py tests/tools/test_clarify_gateway.py -q`

Maintenance notes:

- End-to-end live Telegram/Discord voice message handling was not tested at commit time.
- Keep clarify-response interception before normal voice/media routing.

## Fast mode and provider priority

### `eb9c7e9cc` — Separate Anthropic fast mode from priority tier

Intent: avoid conflating Anthropic fast-mode behavior with generic priority-tier handling.

Touched areas:

- `cli.py`
- `gateway/run.py`
- `hermes_cli/config.py`
- `tests/cli/test_fast_command.py`
- `tests/gateway/test_fast_command.py`

User-visible behavior:

- `/fast` remains the user command, but Anthropic fast handling is kept separate from OpenAI-style priority processing.
- This matters for users switching providers or checking `/fast status` because the same word “fast” maps to different provider mechanisms.

Maintenance notes:

- When updating `/fast` docs, describe provider-specific semantics rather than implying one universal priority flag.
- Keep CLI and gateway behavior aligned; both paths were changed in this commit.

## Persistent goals and compression splits

### `6351d442a` — Preserve goals across compression session splits

Intent: keep an active or paused `/goal` alive when context compression rotates the session id.

Touched areas:

- `gateway/run.py`
- `hermes_cli/goals.py`
- `tests/hermes_cli/test_goals.py`

User-visible behavior:

- A long-running `/goal` should continue after automatic or manual compression creates a continuation session.
- The old session goal is marked cleared after migration so two sessions do not both continue the same goal.

Operational note:

- Running gateway processes need restart/reload before this fix takes effect.

Maintenance notes:

- The active bug was caused by goal state being keyed as `goal:<session_id>` while compression switched to a new session id.
- If upstream merges a similar fix, compare migration semantics carefully: destination state must not be overwritten if it already exists.

Primary docs added:

User-visible behavior: none in Hermes runtime; this is planning/developer documentation.

Maintenance notes:

- Treat these as draft specs, not implemented runtime behavior.
- If later code implements them, add a new local-change entry linking code commits back to these specs.

## Tavily multi-key pool

### `27815ffb8` — Keep Tavily usable across pooled keys

Intent: let operators put multiple Tavily keys in env and use them fill-first without wasting quota on proactive health probes.

Touched areas:

- `plugins/web/tavily/provider.py`
- `tools/web_tools.py`
- `hermes_cli/config.py`
- `hermes_cli/status.py`
- `hermes_cli/dump.py`
- `tests/conftest.py`
- `tests/hermes_cli/test_config.py`
- `tests/tools/test_web_tools_config.py`
- `tests/tools/test_web_tools_tavily.py`
- website docs under `website/docs/` and zh-Hans tool-gateway docs

User-visible behavior:

- `TAVILY_API_KEYS` accepts comma/newline/semicolon-separated values or a JSON array.
- `TAVILY_API_KEY` remains backward compatible and can also contain multiple separated keys.
- The plugin tries keys in configured order, keeps using the first available key, and only cools a key down after a real request hits quota/auth-style failure.
- No background probing is performed.
- Cooled key state is stored under `~/.hermes/rate_limits/tavily_keys.json` using key fingerprints, not raw secrets.
- Default cooldown is 172800 seconds and can be changed with `TAVILY_KEY_COOLDOWN_SECONDS`.

Product docs updated:

- `website/docs/user-guide/features/web-search.md`
- `website/docs/user-guide/configuration.md`
- `website/docs/reference/environment-variables.md`
- `website/docs/reference/tools-reference.md`
- `website/docs/integrations/index.md`
- `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/features/tool-gateway.md`

Verification recorded in commit:

- `.venv/bin/pytest -q tests/tools/test_web_tools_tavily.py tests/tools/test_web_tools_config.py tests/hermes_cli/test_config.py tests/hermes_cli/test_status.py`
- `.venv/bin/ruff check plugins/web/tavily/provider.py tools/web_tools.py hermes_cli/config.py hermes_cli/status.py hermes_cli/dump.py tests/conftest.py tests/tools/test_web_tools_tavily.py tests/tools/test_web_tools_config.py tests/hermes_cli/test_config.py`

Maintenance notes:

- Do not change this to round-robin unless the goal changes from quota exhaustion to even load distribution.
- Do not add proactive health checks; real requests are the recovery mechanism.

## Gateway attachment delivery visibility

### `4cab4b729` — Make attachment delivery failures visible

Intent: prevent false-positive delivery success when text sends correctly but a requested file or archive attachment fails to upload.

Touched areas:

- `gateway/platforms/base.py`
- `gateway/run.py`
- Gateway attachment, topic-session, TTS media-routing, and send-message tests

User-visible behavior:

- Required file delivery failures are reported automatically in English.
- Archive-style paths such as `.tar.xz` are included in MEDIA/local-file delivery routing instead of being silently skipped.
- Streaming and non-streaming delivery paths share the same incomplete-delivery visibility contract.

Verification recorded in commit:

- `pytest tests/gateway/test_send_image_file.py::TestExtractMediaImages tests/gateway/test_base_topic_sessions.py::TestBasePlatformTopicSessions tests/gateway/test_tts_media_routing.py tests/gateway/test_stream_consumer.py tests/tools/test_send_message_tool.py tests/tools/test_signal_media.py -q`
- `python -m py_compile gateway/platforms/base.py gateway/run.py`
- `git diff --check`

Maintenance notes:

- Keep MEDIA parser extension support aligned with local-file delivery extensions.
- Preserve explicit failure notices for required attachments; text delivery alone must not imply total success.
- Live gateway/platform upload smoke with real Telegram API was not run at commit time.

## Background review skill creation approval

### `1c4de266c` — Require approval before background review creates skills

Intent: keep background review non-blocking while preventing it from creating or modifying skills without explicit operator approval.

Touched areas:

- `agent/background_review.py`
- `tools/memory_tool.py`
- `tools/skill_manager_tool.py`
- `gateway/run.py`
- `gateway/platforms/telegram.py`
- `hermes_cli/commands.py`
- Memory, skill-manager, background-review, review-prompt, gateway proposal, and Telegram command tests

User-visible behavior:

- Background review may still propose useful memory or skill improvements.
- Skill creation now routes through an approval/proposal flow instead of being silently applied by the review fork.
- Telegram surfaces proposal approval affordances for the operator.

Verification recorded in commit:

- `scripts/run_tests.sh tests/tools/test_memory_tool.py tests/tools/test_skill_manager_tool.py tests/run_agent/test_review_prompt_class_first.py tests/run_agent/test_background_review_toolset_restriction.py tests/gateway/test_skill_proposal_command.py`
- `python3 -m py_compile agent/background_review.py tools/memory_tool.py tools/skill_manager_tool.py gateway/run.py gateway/platforms/telegram.py hermes_cli/commands.py`
- `git diff --check`

Maintenance notes:

- Background review must not call `clarify` or otherwise wait for user input.
- Keep the review fork memory/skills-only at runtime.
- Preserve the approval boundary for skill creation even if upstream changes the background-review prompt or toolset plumbing.

## Fork identity and release intake

### `fe67c67b9` — Make fork identity explicit before branch normalization

Intent: make this checkout read as a personal, needs-driven fork rather than an upstream PR staging mirror.

Touched areas:

- `README.md`
- `docs/local-changes/README.md`
- `docs/local-changes/branch-strategy.md`

Developer-facing behavior:

- The README states that the fork maintains local operational changes while following useful official upstream releases.
- Branch strategy docs define `origin` as the personal fork and `upstream` as the release/tag intake remote.
- Historical topic branches should stay out of the normal branch list unless actively maintained.

Verification recorded in commit:

- Documentation-only change reviewed with `git diff`.

Maintenance notes:

- Keep README wording aligned with the branch strategy: personal fork first, upstream release tracking preserved.
- Do not remove the `upstream` remote or tag-fetch path when cleaning branch clutter.
