# Restored Features from v0.15 Custom Branch

This document tracks features from the v0.15 custom branch that have been restored to the v0.17 port.

During the v0.17 port, 98 custom commits (squashed from 56 original commits) were reduced to 2 commits, losing 9 features. This restoration effort brings back those features with proper attribution and testing.

## Restored Features

### 1. Primary Provider Model Fallbacks
**Commit:** 279cfbb61, c20869394, d90fdb23d  
**Original commits:** Multiple commits addressing model fallback routing  
**Status:** ✅ Restored

**What it does:**
- Ensures model fallback routing prefers models from the same provider before falling back to a different provider
- Fixes cron auth fallback model routing to respect provider boundaries
- Documents cron fallback provider support

**Files changed:**
- `run_agent.py`: Model fallback logic
- `docs/`: Cron fallback provider documentation

**Tests:** Existing test suite passes

---

### 2. Preserve Goals Across Compression Session Splits
**Commit:** 631e3a406  
**Original commit:** 5aa3c5dce  
**Status:** ✅ Restored

**What it does:**
- When context compaction occurs, the user's goals are preserved and injected into the next session
- Prevents loss of task continuity across automatic session splits

**Files changed:**
- `agent/conversation_loop.py`: Goal preservation in compaction handler

**Tests:** Existing test suite passes

---

### 3. Approval Reviewer Fallback Chain
**Commit:** 63c588bdf  
**Original commit:** 0fd76c412  
**Status:** ✅ Restored

**What it does:**
- Uses configured reviewer fallback chain when primary approval reviewer fails
- Improves robustness of approval workflows

**Files changed:**
- `run_agent.py`: Approval reviewer selection logic

**Tests:** Existing test suite passes

---

### 4. Telegram Working Bubble Deduplication
**Commit:** 541a0bc6e  
**Original commit:** 02d85f74d  
**Status:** ✅ Restored

**What it does:**
- Avoids duplicate Telegram working status bubbles during transient heartbeat edit failures
- Improves UX by preventing visual noise

**Files changed:**
- Telegram integration modules

**Tests:** Existing test suite passes

---

### 5. Stale Stream Failover
**Commit:** 449687de7  
**Original commit:** 9c3e6c6a3  
**Status:** ✅ Restored

**What it does:**
- Detects when a streaming provider becomes unresponsive (no chunks for 60s)
- Automatically fails over to the next available provider
- Prevents indefinite hangs on stale streams

**Files changed:**
- `run_agent.py`: `_chat_completion_with_cache()` streaming loop
- Tests: `tests/run_agent/test_stale_stream_failover.py` (5 tests, all pass)

**Key behavior:**
- 60-second chunk timeout triggers failover
- Only applies to streaming calls
- Preserves existing provider-error fallback chain
- Falls through to next provider in stack

---

### 6. Memory Read-Before-Write Guard
**Commit:** 1c5d42b82  
**Original commit:** 6fa29bc07  
**Status:** ✅ Restored

**What it does:**
- Requires `memory(action='read', target=X)` before any add/replace/remove to the same target within a turn
- Prevents blind overwrites and encourages comparison against current state
- Conservative review prompts steer "maintenance-only" behavior

**Files changed:**
- `agent/background_review.py`: Conservative review prompts
- `tools/memory_tool.py`: Added read() method and schema updates
- `run_agent.py`: Centralized dispatch with read-gate logic
- `agent/agent_init.py`: Initialize per-turn read state
- `agent/turn_context.py`: Reset read state per turn
- `agent/tool_executor.py`: Use centralized dispatch (concurrent path)
- `agent/agent_runtime_helpers.py`: Use centralized dispatch (sequential path)
- Tests: 6 test modules updated/added (615 assertions pass)

**Key behavior:**
- Per-turn gate: read state resets at start of each turn
- Single-op mutations gated; batch operations ungated (review prompts handle steering)
- Read gate only applies to `memory` and `user` targets
- Successful read/write resets memory nudge counter

---

## Already Present (in original squash commit 1f1070538)

The following features from the v0.15 custom branch were **already included** in the v0.17 port's initial squash commit and did not require restoration:

1. **Vision fallback model resolution** — `auxiliary_client.py` hardened fallback model resolution
2. **Env file attachments** — `.env` / `.env.*` / `*.env.*` treated as text attachments via MEDIA tags, local paths, Telegram documents
3. **Telegram proxy rotation** — Multi-proxy support with automatic rotation on failure
4. **Clarify audio reply** — Handles audio message replies in Telegram conversations  
5. **Tavily multi-key rotation** — Multi-API-key rotation provider for Tavily web search
6. **Web_tools auxiliary routing** — Routes auxiliary model calls properly for web_tools

These were preserved during the original v0.17 port and verified with 666 passed, 2 skipped baseline.

---

## Skipped Features

The following features from the v0.15 custom branch were evaluated but **not** restored to v0.17:

### 1. Telegram Topic Compression Rebind
**Original commit:** bd5f627a2  
**Reason:** v0.17 implements superior solution

v0.17 already uses comprehensive `_sync_telegram_topic_binding()` with read-path self-healing. Restoring the old single-point rebind would create dual code paths and potential conflicts. The new implementation is more robust.

---

### 2. Provider-Level Browser Headers
**Original commit:** b08c43284  
**Reason:** Depends on deleted upstream code

This feature modified the AI Gateway provider, which upstream deleted in commit `febc4cfec`. Restoring it would require resurrecting deleted infrastructure, violating the upstream's architectural decision.

---

### 3. fd/fdfind File Search Fallback
**Original commit:** 3c3d51d09  
**Status:** Requires verification

Plan initially claimed this was present in v0.17 port, but quick checks found no evidence of `_get_fd_command` or `_search_files_fd` in `tools/file_operations.py`. This may be a false positive in the original analysis and might actually need restoration. **Action item:** Verify presence and restore if missing.

---

## Testing

All restored features have been verified against the existing test suite:
- Baseline: ≥666 passed, 2 skipped
- New tests added for stale stream failover (5 tests)
- New tests added for memory read-before-write (15+ assertions)

Full test suite run: `uv run pytest -q`

---

## Attribution

All restored commits preserve original authorship (Oxidane-bot) and include co-authorship attribution to Claude Opus 4.8.

Original commit messages have been preserved where possible, with notes added to indicate v0.17 module-split reimplementation where applicable.
