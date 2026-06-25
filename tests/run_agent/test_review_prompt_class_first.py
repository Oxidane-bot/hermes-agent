"""Behavior tests for conservative background memory / skill review prompts."""

from run_agent import AIAgent


def test_skill_review_prompt_is_maintenance_only():
    prompt = AIAgent._SKILL_REVIEW_PROMPT
    lower = prompt.lower()
    assert "maintain the skill library only" in lower
    assert "reusable" in lower
    assert "conversation notes" in lower
    assert "most sessions produce" not in lower
    assert "missed learning opportunity" not in lower


def test_skill_review_prompt_prefers_existing_skills_before_create():
    prompt = AIAgent._SKILL_REVIEW_PROMPT
    lower = prompt.lower()
    assert "skill_view" in prompt
    assert "skills_list" in prompt
    assert "prefer patching" in lower
    assert "existing skill" in lower
    assert "create a new class-level skill only" in lower


def test_skill_review_prompt_names_support_file_kinds():
    prompt = AIAgent._SKILL_REVIEW_PROMPT
    assert "references/" in prompt
    assert "templates/" in prompt
    assert "scripts/" in prompt
    lower = prompt.lower()
    assert "provider quirks" in lower or "evidence" in lower
    assert "starter" in lower
    assert "repeatable" in lower or "verification" in lower


def test_skill_review_prompt_keeps_user_corrections_scoped_to_task_class():
    prompt = AIAgent._SKILL_REVIEW_PROMPT
    lower = prompt.lower()
    assert "style" in lower
    assert "sequence" in lower
    assert "tooling" in lower
    assert "verification" in lower
    assert "same task class" in lower
    assert "the user said" in lower


def test_skill_review_prompt_rejects_status_and_one_off_context():
    prompt = AIAgent._SKILL_REVIEW_PROMPT
    lower = prompt.lower()
    assert "project status" in lower
    assert "completed work" in lower
    assert "temporary" in lower
    assert "one-off context" in lower
    assert "nothing to save." in lower


def test_combined_review_prompt_defaults_to_no_action():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    lower = prompt.lower()
    assert "default to no action" in lower
    assert "durable update" in lower
    assert "nothing to save." in lower


def test_combined_review_prompt_requires_memory_read_first():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    assert "memory(action='read', target='user')" in prompt
    assert "memory(action='read'" in prompt
    lower = prompt.lower()
    assert "prefer replace/remove over add" in lower
    assert "add is the last resort" in lower


def test_combined_review_prompt_separates_memory_and_skill_scopes():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    lower = prompt.lower()
    assert "memory scope" in lower
    assert "skill scope" in lower
    assert "who the user is" in lower
    assert "stable execution environment" in lower
    assert "how to do work" in lower


def test_combined_review_prompt_blocks_historical_logs():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    lower = prompt.lower()
    assert "do not create historical logs" in lower
    assert "project status" in lower
    assert "task results" in lower
    assert "debug history" in lower
    assert "schedules" in lower


def test_combined_review_prompt_keeps_skill_update_conservative():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    lower = prompt.lower()
    assert "skills_list/skill_view" in prompt
    assert "prefer patching" in lower
    assert "existing skill" in lower
    assert "create a new class-level skill only" in lower


def test_memory_review_prompt_requires_read_before_write():
    prompt = AIAgent._MEMORY_REVIEW_PROMPT
    assert "memory(action='read', target='user')" in prompt
    assert "memory(action='read', target='memory')" in prompt
    lower = prompt.lower()
    assert "prefer replace" in lower
    assert "add is the last resort" in lower


def test_memory_review_prompt_rejects_one_off_memory():
    prompt = AIAgent._MEMORY_REVIEW_PROMPT
    lower = prompt.lower()
    assert "project status" in lower
    assert "one-off corrections" in lower
    assert "task results" in lower
    assert "debug history" in lower
    assert "workflow procedures" in lower
    assert "skills_list" not in prompt
    assert "nothing to save." in lower
