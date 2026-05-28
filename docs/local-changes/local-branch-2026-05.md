# May 2026 Local Branch Changes

Baseline: `fork/local` at `eb76cdd740dc4dcf07542eb7c217b642d03b763b`.
Current local head when this document was created: `27815ffb8`.

This file documents the local-only commits above the fork baseline so future maintenance can tell which behavior is intentional and which can be dropped when upstream catches up.

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
