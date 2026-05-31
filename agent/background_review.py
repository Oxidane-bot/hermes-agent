"""Background memory/skill review — fork the agent to evaluate the turn.

After every turn, ``AIAgent.run_conversation`` may call
:func:`spawn_background_review` to fire off a daemon thread that replays
the conversation snapshot in a forked :class:`AIAgent` and asks itself
"should any skill/memory be saved or updated?".  Writes go straight to
the memory + skill stores.  Main conversation and prompt cache are never
touched.

The fork inherits the parent's live runtime (provider, model, base_url,
credentials, cached system prompt) so it hits the same prefix cache and
uses the same auth.  It runs with a tool whitelist limited to memory and
skill management tools; everything else is denied at runtime.

See the ``hermes-agent-dev`` skill (``references/self-improvement-loop.md``)
for invariants and PR review criteria.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Review-prompt strings — used by ``spawn_background_review_thread`` to build
# the user-message that the forked review agent receives.  AIAgent exposes
# them as class attributes (``_MEMORY_REVIEW_PROMPT`` etc.) for back-compat;
# the actual text lives here so future edits are one-place.
_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and conservatively decide whether a durable "
    "memory update is warranted. 'Nothing to save.' is a valid successful result.\n\n"
    "Before changing a target, read it with memory(action='read', target=...).\n\n"
    "Durability gates:\n"
    "1. USER profile is only for stable user identity and long-lived preferences: "
    "name, role, timezone, communication style, or explicit standing preferences.\n"
    "2. MEMORY is only for durable environment/runtime facts: stable project paths, "
    "configured tools, reusable local conventions, or long-lived setup facts.\n"
    "3. Do not save task progress, one-off outcomes, transient failures, or "
    "skill-specific operational facts to global memory or the user profile.\n"
    "4. If the lesson is procedural, patch an existing skill or propose a new skill; "
    "do not put procedural skill instructions in memory.\n\n"
    "If nothing passes these gates, say exactly 'Nothing to save.' and stop."
)

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and conservatively decide whether the skill "
    "library needs a durable update. 'Nothing to save.' is a valid successful "
    "result and should be used whenever no durable skill lesson exists.\n\n"
    "Read before write: use memory(action='read', target='memory') or "
    "memory(action='read', target='user') before relying on or changing those "
    "stores. Use skills_list and skill_view before patching a skill or proposing "
    "a new one.\n\n"
    "Durability gates:\n"
    "  • USER profile is only for stable user identity and long-lived preferences.\n"
    "  • MEMORY is only for durable environment/runtime facts.\n"
    "  • Skill-specific operational facts, workflow steps, tool recipes, and "
    "debugging procedures must not be saved to global memory or the user profile.\n"
    "  • New skills must be proposed with skill_manage(action='propose_create'), "
    "not directly created. The user approves or rejects the proposal later.\n\n"
    "When there is durable skill signal, prefer the earliest safe action that fits:\n"
    "  1. PATCH A CURRENTLY-LOADED SKILL. Look for skills loaded via /skill-name "
    "or inspected via skill_view. If one covers the learning, patch that skill.\n"
    "  2. PATCH AN EXISTING UMBRELLA. Use skills_list + skill_view to find a "
    "class-level skill and patch it.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella with "
    "skill_manage action=write_file. Use references/<topic>.md for concise "
    "knowledge/research/API-doc notes, templates/<name>.<ext> for starter files "
    "meant to be copied and modified, and scripts/<name>.<ext> for statically "
    "re-runnable verification/probe actions. Add a one-line pointer in SKILL.md.\n"
    "  4. PROPOSE A NEW CLASS-LEVEL UMBRELLA SKILL only when no existing skill "
    "covers the class. Use skill_manage(action='propose_create', name=..., "
    "content=..., category=...). The name MUST be class level and MUST NOT be a "
    "PR number, exact error string, feature codename, library-alone name, or "
    "'fix-X / debug-Y / audit-Z-today' session artifact.\n\n"
    "User-preference corrections about style, format, tone, verbosity, legibility, "
    "or workflow (for example 'stop doing X', 'don't format like this', or "
    "'I hate when you Y') are first-class skill signals only when they apply to "
    "a repeatable task class. Put durable preference lessons in SKILL.md, not only "
    "memory; memory says who the user is, skills say how to do this class of task.\n\n"
    "If you notice overlapping existing skills, mention it in your reply; the "
    "background curator handles consolidation.\n\n"
    "Protected skills (DO NOT edit these): bundled skills shipped with Hermes and "
    "hub-installed skills. Pinned skills may be patched but not deleted. If only "
    "protected skills would need changes, say 'Nothing to save.' and stop.\n\n"
    "Do NOT capture as skills:\n"
    "  • Environment-dependent failures: missing binaries, fresh-install errors, "
    "post-migration path mismatches, command not found, unconfigured credentials, "
    "or uninstalled packages.\n"
    "  • Negative claims about tools or features ('browser tools do not work', "
    "'X tool is broken', 'cannot use Y').\n"
    "  • Session-specific transient errors that resolved before the conversation ended.\n"
    "  • One-off task narratives.\n"
    "If a tool failed because of setup state, capture the fix under an existing "
    "setup/troubleshooting skill only when it is durable; capture the fix, not "
    "the failure as a constraint.\n\n"
    "If no durable skill update or proposal passes these gates, say exactly "
    "'Nothing to save.' and stop."
)

_COMBINED_REVIEW_PROMPT = (
    "Review the conversation above and conservatively decide whether either "
    "memory or skills need a durable update. 'Nothing to save.' is a valid "
    "successful result.\n\n"
    "Read before write: use memory(action='read', target='memory'/'user') before "
    "changing or relying on a memory target, and use skills_list + skill_view "
    "before patching skills or proposing a new one.\n\n"
    "**Memory**: save only durable facts. USER profile is only for stable user "
    "identity and long-lived preferences. MEMORY is only for durable "
    "environment/runtime facts. Do not save task progress, one-off outcomes, "
    "transient failures, or skill-specific operational facts to global memory or "
    "the user profile. Use the memory tool only when a fact passes those gates.\n\n"
    "**Skills**: skills say how to do a repeatable class of task. New skills must "
    "be proposed with skill_manage(action='propose_create'), not directly created.\n\n"
    "Skill action preference order:\n"
    "  1. PATCH A CURRENTLY-LOADED SKILL. Check /skill-name loads and skill_view.\n"
    "  2. PATCH AN EXISTING UMBRELLA found via skills_list + skill_view.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella: references/<topic>.md for "
    "knowledge/research/API-doc notes, templates/<name>.<ext> for starter files "
    "to copy/modify, scripts/<name>.<ext> for re-runnable verification/probes; "
    "add a SKILL.md pointer.\n"
    "  4. PROPOSE A NEW CLASS-LEVEL UMBRELLA only when nothing exists. The name "
    "MUST be class level and MUST NOT be a PR number, exact error string, feature "
    "codename, library-alone name, or today's fix/debug/audit artifact.\n\n"
    "User corrections about style, format, tone, verbosity, legibility, or "
    "workflow (for example 'stop doing X', 'don't format like this', or 'I hate "
    "when you Y') are first-class skill signals only when repeatable. Put durable "
    "task-class preference lessons in SKILL.md; memory says who the user is and "
    "skills say how to do the task class.\n\n"
    "If existing skills overlap, mention it for the curator instead of consolidating.\n\n"
    "Protected skills (DO NOT edit): bundled and hub-installed skills. Pinned "
    "skills may be patched but not deleted. If only protected skills would need "
    "changes, say 'Nothing to save.' and stop.\n\n"
    "Do NOT capture as skills: environment-dependent failures (missing binaries, "
    "fresh-install errors, command not found, unconfigured credentials, "
    "uninstalled packages), negative claims ('do not work', 'is broken'), "
    "session-specific transient errors, or one-off task narratives. Capture the "
    "fix under an existing setup/troubleshooting skill only when durable; capture "
    "the fix, not the failure.\n\n"
    "Act only on durable signal. If nothing passes the gates, say exactly "
    "'Nothing to save.' and stop."
)


def summarize_background_review_action_details(
    review_messages: List[Dict],
    prior_snapshot: List[Dict],
) -> List[Dict[str, Any]]:
    """Build structured successful-action details for a background review pass.

    Walks the review agent's session messages and collects "successful tool
    action" descriptions to surface to the user (e.g. "Memory updated").
    Tool messages already present in ``prior_snapshot`` are skipped so we
    don't re-surface stale results from the prior conversation that the
    review agent inherited via ``conversation_history`` (issue #14944).

    Matching is by ``tool_call_id`` when available, with a content-equality
    fallback for tool messages that lack one.
    """
    existing_tool_call_ids = set()
    existing_tool_contents = set()
    for prior in prior_snapshot or []:
        if not isinstance(prior, dict) or prior.get("role") != "tool":
            continue
        tcid = prior.get("tool_call_id")
        if tcid:
            existing_tool_call_ids.add(tcid)
        else:
            content = prior.get("content")
            if isinstance(content, str):
                existing_tool_contents.add(content)

    actions: List[Dict[str, Any]] = []
    for msg in review_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        tcid = msg.get("tool_call_id")
        if tcid and tcid in existing_tool_call_ids:
            continue
        if not tcid:
            content_str = msg.get("content")
            if isinstance(content_str, str) and content_str in existing_tool_contents:
                continue
        try:
            data = json.loads(msg.get("content", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not data.get("success"):
            continue
        message = data.get("message", "")
        target = data.get("target", "")
        lower_message = message.lower()
        if data.get("action") == "skill_proposal" or data.get("proposal_id"):
            actions.append({
                "kind": "skill_proposal",
                "summary": message or f"Skill proposal '{data.get('name', '')}' created.",
                "proposal_id": data.get("proposal_id", ""),
                "name": data.get("name", ""),
                "category": data.get("category"),
                "proposal_path": data.get("proposal_path"),
            })
        elif "created" in lower_message:
            actions.append({"kind": "skill_created", "summary": message})
        elif "updated" in lower_message or "patched" in lower_message or "written" in lower_message:
            actions.append({"kind": "skill_patch", "summary": message})
        elif "added" in lower_message or (target and "add" in lower_message):
            label = "Memory" if target == "memory" else "User profile" if target == "user" else target
            actions.append({"kind": "memory_update", "summary": f"{label} updated", "target": target})
        elif "Entry added" in message:
            label = "Memory" if target == "memory" else "User profile" if target == "user" else target
            actions.append({"kind": "memory_update", "summary": f"{label} updated", "target": target})
        elif "removed" in lower_message or "replaced" in lower_message:
            label = "Memory" if target == "memory" else "User profile" if target == "user" else target
            actions.append({"kind": "memory_update", "summary": f"{label} updated", "target": target})
    return actions


def summarize_background_review_actions(
    review_messages: List[Dict],
    prior_snapshot: List[Dict],
) -> List[str]:
    """Build the legacy string action summary for CLI/TUI compatibility."""
    return [
        str(action.get("summary", ""))
        for action in summarize_background_review_action_details(review_messages, prior_snapshot)
        if action.get("summary")
    ]


def build_memory_write_metadata(
    agent: Any,
    *,
    write_origin: Optional[str] = None,
    execution_context: Optional[str] = None,
    task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build provenance metadata for external memory-provider mirrors."""
    metadata: Dict[str, Any] = {
        "write_origin": write_origin or getattr(agent, "_memory_write_origin", "assistant_tool"),
        "execution_context": (
            execution_context
            or getattr(agent, "_memory_write_context", "foreground")
        ),
        "session_id": agent.session_id or "",
        "parent_session_id": agent._parent_session_id or "",
        "platform": agent.platform or os.environ.get("HERMES_SESSION_SOURCE", "cli"),
        "tool_name": "memory",
    }
    if task_id:
        metadata["task_id"] = task_id
    if tool_call_id:
        metadata["tool_call_id"] = tool_call_id
    return {k: v for k, v in metadata.items() if v not in {None, ""}}


def _run_review_in_thread(
    agent: Any,
    messages_snapshot: List[Dict],
    prompt: str,
) -> None:
    """Worker function executed in the background-review daemon thread.

    Spawns a forked ``AIAgent`` inheriting the parent's runtime, runs the
    review prompt, and surfaces a compact action summary back to the user
    via ``agent._safe_print`` and ``agent.background_review_callback``.
    """
    # Local import to avoid a hard circular dep at module load.
    from run_agent import AIAgent
    from tools.terminal_tool import set_approval_callback as _set_approval_callback

    # Install a non-interactive approval callback on this worker
    # thread so any dangerous-command guard the review agent trips
    # resolves to "deny" instead of falling back to input() -- which
    # deadlocks against the parent's prompt_toolkit TUI (#15216).
    # Same pattern as _subagent_auto_deny in tools/delegate_tool.py.
    def _bg_review_auto_deny(command, description, **kwargs):
        logger.warning(
            "Background review auto-denied dangerous command: %s (%s)",
            command, description,
        )
        return "deny"
    try:
        _set_approval_callback(_bg_review_auto_deny)
    except Exception:
        pass

    review_agent = None
    review_messages: List[Dict] = []
    try:
        with open(os.devnull, "w", encoding="utf-8") as _devnull, \
             contextlib.redirect_stdout(_devnull), \
             contextlib.redirect_stderr(_devnull):
            # Inherit the parent agent's live runtime (provider, model,
            # base_url, api_key, api_mode) so the fork uses the exact
            # same credentials the main turn is using.  Without this,
            # AIAgent.__init__ re-runs auto-resolution from env vars,
            # which fails for OAuth-only providers, session-scoped
            # creds, or credential-pool setups where the resolver can't
            # reconstruct auth from scratch -- producing the spurious
            # "No LLM provider configured" warning at end of turn.
            _parent_runtime = agent._current_main_runtime()
            _parent_api_mode = _parent_runtime.get("api_mode") or None
            # The review fork needs to call agent-loop tools (memory,
            # skill_manage). Those tools require Hermes' own dispatch,
            # which the codex_app_server runtime bypasses entirely
            # (it runs the turn inside codex's subprocess). So when
            # the parent is on codex_app_server, downgrade the review
            # fork to codex_responses — same auth/credentials, but
            # talks to the OpenAI Responses API directly so Hermes
            # owns the loop and the agent-loop tools dispatch.
            if _parent_api_mode == "codex_app_server":
                _parent_api_mode = "codex_responses"
            # skip_memory=True keeps the review fork from
            # touching external memory plugins (honcho, mem0,
            # supermemory, etc.).  Without it, the fork's
            # __init__ rebuilds its own _memory_manager from
            # config, scoped to the parent's session_id, and
            # run_conversation() then leaks the harness prompt
            # into the user's real memory namespace via three
            # ingestion sites: on_turn_start (cadence + turn
            # message), prefetch_all (recall query), and
            # sync_all (harness prompt + review output recorded
            # as a (user, assistant) turn pair).  Built-in
            # MEMORY.md / USER.md state is re-bound from the
            # parent below so memory(action="add") writes from
            # the review still land on disk; the review just
            # has zero side effects on external providers.
            # Match parent's toolset config so ``tools[]`` is byte-identical
            # in the request body — Anthropic's cache key includes it.
            # (The runtime whitelist below still restricts dispatch.)
            review_agent = AIAgent(
                model=agent.model,
                max_iterations=16,
                quiet_mode=True,
                platform=agent.platform,
                provider=agent.provider,
                api_mode=_parent_api_mode,
                base_url=_parent_runtime.get("base_url") or None,
                api_key=_parent_runtime.get("api_key") or None,
                credential_pool=getattr(agent, "_credential_pool", None),
                parent_session_id=agent.session_id,
                enabled_toolsets=getattr(agent, "enabled_toolsets", None),
                disabled_toolsets=getattr(agent, "disabled_toolsets", None),
                skip_memory=True,
            )
            review_agent._memory_write_origin = "background_review"
            review_agent._memory_write_context = "background_review"
            review_agent._memory_store = agent._memory_store
            review_agent._memory_enabled = agent._memory_enabled
            review_agent._user_profile_enabled = agent._user_profile_enabled
            review_agent._memory_nudge_interval = 0
            review_agent._skill_nudge_interval = 0
            # Suppress all status/warning emits from the fork so the
            # user only sees the final successful-action summary.
            # Without this, mid-review "Iteration budget exhausted",
            # rate-limit retries, compression warnings, and other
            # lifecycle messages bubble up through _emit_status ->
            # _vprint and leak past the stdout redirect (they go via
            # _print_fn/status_callback, which bypass sys.stdout).
            review_agent.suppress_status_output = True
            # Inherit the parent's cached system prompt verbatim so
            # the review fork's outbound HTTP request hits the same
            # Anthropic/OpenRouter prefix cache the parent warmed.
            # Without this, the fork rebuilds the system prompt from
            # scratch (fresh _hermes_now() timestamp, fresh
            # session_id, narrower toolset → different skills_prompt)
            # and the byte-exact prefix-cache key misses. See
            # issue #25322 and PR #17276 for the full analysis +
            # measured impact (~26% end-to-end cost reduction on
            # Sonnet 4.5).
            review_agent._cached_system_prompt = agent._cached_system_prompt
            # Defensive: pin session_start + session_id to the
            # parent's so any code path that re-renders parts of
            # the system prompt (compression, plugin hooks) still
            # produces byte-identical output. The cached-prompt
            # assignment above already short-circuits the normal
            # rebuild path, but these pins guarantee parity even
            # if a future code path bypasses the cache.
            review_agent.session_start = agent.session_start
            review_agent.session_id = agent.session_id

            from model_tools import get_tool_definitions
            from hermes_cli.plugins import (
                set_thread_tool_whitelist,
                clear_thread_tool_whitelist,
            )

            review_whitelist = {
                t["function"]["name"]
                for t in get_tool_definitions(
                    enabled_toolsets=["memory", "skills"],
                    quiet_mode=True,
                )
            }
            set_thread_tool_whitelist(
                review_whitelist,
                deny_msg_fmt=(
                    "Background review denied non-whitelisted tool: "
                    "{tool_name}. Only memory/skill tools are allowed."
                ),
            )
            try:
                review_agent.run_conversation(
                    user_message=(
                        prompt
                        + "\n\nYou can only call memory and skill "
                        "management tools. Other tools will be denied "
                        "at runtime — do not attempt them."
                    ),
                    conversation_history=messages_snapshot,
                )
            finally:
                clear_thread_tool_whitelist()

            # Snapshot review actions before teardown. close() is allowed to
            # clean per-session state, but the user-visible self-improvement
            # summary still needs the completed review agent's tool results.
            review_messages = list(getattr(review_agent, "_session_messages", []))

            # Tear down memory providers while stdout is still
            # redirected so background thread teardown (Honcho flush,
            # Hindsight sync, etc.) stays silent.  The finally block
            # below is a safety net for the exception path.
            try:
                review_agent.shutdown_memory_provider()
            except Exception:
                pass
            try:
                review_agent.close()
            except Exception:
                pass
            review_agent = None

        # Scan the review agent's messages for successful tool actions
        # and surface a compact summary to the user. Tool messages
        # already present in messages_snapshot must be skipped, since
        # the review agent inherits that history and would otherwise
        # re-surface stale "created"/"updated" messages from the prior
        # conversation as if they just happened (issue #14944).
        action_details = summarize_background_review_action_details(
            review_messages,
            messages_snapshot,
        )
        actions = [
            str(action.get("summary", ""))
            for action in action_details
            if action.get("summary")
        ]

        if actions:
            summary = " · ".join(dict.fromkeys(actions))
            agent._safe_print(
                f"  💾 Self-improvement review: {summary}"
            )
            _bg_cb = agent.background_review_callback
            if _bg_cb:
                try:
                    _bg_cb(
                        f"💾 Self-improvement review: {summary}"
                    )
                except Exception:
                    pass

        proposal_cb = getattr(agent, "background_review_proposal_callback", None)
        if proposal_cb:
            seen_proposals = set()
            for action in action_details:
                if action.get("kind") != "skill_proposal":
                    continue
                proposal_id = str(action.get("proposal_id") or "")
                if not proposal_id or proposal_id in seen_proposals:
                    continue
                seen_proposals.add(proposal_id)
                try:
                    proposal_cb(action)
                except Exception:
                    pass

    except Exception as e:
        logger.warning("Background memory/skill review failed: %s", e)
        agent._emit_auxiliary_failure("background review", e)
    finally:
        # Safety-net cleanup for the exception path.  Normal
        # completion already shut down inside redirect_stdout above.
        # Re-open devnull here so any teardown output (Honcho flush,
        # Hindsight sync, background thread joins) stays silent even
        # on the exception path where redirect_stdout already exited.
        if review_agent is not None:
            try:
                with open(os.devnull, "w", encoding="utf-8") as _fn, \
                     contextlib.redirect_stdout(_fn), \
                     contextlib.redirect_stderr(_fn):
                    try:
                        review_agent.shutdown_memory_provider()
                    except Exception:
                        pass
                    try:
                        review_agent.close()
                    except Exception:
                        pass
            except Exception:
                pass
        # Clear the approval callback on this bg-review thread so a
        # recycled thread-id doesn't inherit a stale reference.
        try:
            _set_approval_callback(None)
        except Exception:
            pass


def spawn_background_review_thread(
    agent: Any,
    messages_snapshot: List[Dict],
    review_memory: bool = False,
    review_skills: bool = False,
):
    """Build the review thread target and prompt for a background review.

    Returns a ``(target, prompt)`` tuple.  The caller (``AIAgent._spawn_background_review``)
    owns the actual ``threading.Thread`` construction so test-level patches
    of ``run_agent.threading.Thread`` keep working.
    """
    # Pick the right prompt based on which triggers fired.  Allow per-agent
    # override (the prompts moved to module-level constants but old code paths
    # that set agent._MEMORY_REVIEW_PROMPT etc. directly keep working).
    if review_memory and review_skills:
        prompt = getattr(agent, "_COMBINED_REVIEW_PROMPT", _COMBINED_REVIEW_PROMPT)
    elif review_memory:
        prompt = getattr(agent, "_MEMORY_REVIEW_PROMPT", _MEMORY_REVIEW_PROMPT)
    else:
        prompt = getattr(agent, "_SKILL_REVIEW_PROMPT", _SKILL_REVIEW_PROMPT)

    def _target() -> None:
        _run_review_in_thread(agent, messages_snapshot, prompt)

    return _target, prompt


__all__ = [
    "_MEMORY_REVIEW_PROMPT",
    "_SKILL_REVIEW_PROMPT",
    "_COMBINED_REVIEW_PROMPT",
    "spawn_background_review_thread",
    "summarize_background_review_actions",
    "summarize_background_review_action_details",
    "build_memory_write_metadata",
]
