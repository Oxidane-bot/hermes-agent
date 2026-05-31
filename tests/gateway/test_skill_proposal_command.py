"""Gateway and Telegram glue for background-review skill proposals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from tools.skill_manager_tool import _propose_create_skill


VALID_SKILL_CONTENT = """\
---
name: draft-skill
description: A draft skill for approval tests.
---

# Draft Skill

Do a durable thing.
"""


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="12345",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    return runner


@pytest.fixture()
def proposal_env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    proposals = tmp_path / "proposals"
    monkeypatch.setattr("tools.skill_manager_tool.SKILLS_DIR", skills)
    monkeypatch.setattr("agent.skill_utils.get_all_skills_dirs", lambda: [skills])
    monkeypatch.setattr("tools.skill_manager_tool._skill_proposals_dir", lambda: proposals)
    return skills, proposals


@pytest.mark.asyncio
async def test_skill_proposal_show_approve_reject_commands(proposal_env):
    skills, _proposals = proposal_env
    proposal = _propose_create_skill("draft-skill", VALID_SKILL_CONTENT)
    proposal_id = proposal["proposal_id"]
    runner = _make_runner()

    shown = await runner._handle_skill_proposal_command(_make_event(f"/skill-proposal show {proposal_id}"))
    assert "draft-skill" in shown
    assert "Approve" in shown

    approved = await runner._handle_skill_proposal_command(_make_event(f"/skill-proposal approve {proposal_id}"))
    assert "approved" in approved
    assert (skills / "draft-skill" / "SKILL.md").exists()

    stale = await runner._handle_skill_proposal_command(_make_event(f"/skill-proposal approve {proposal_id}"))
    assert "already approved" in stale

    proposal2 = _propose_create_skill("draft-skill-2", VALID_SKILL_CONTENT.replace("draft-skill", "draft-skill-2"))
    proposal2_id = proposal2["proposal_id"]
    rejected = await runner._handle_skill_proposal_command(_make_event(f"/skill-proposal reject {proposal2_id}"))
    assert "rejected" in rejected
    assert not (skills / "draft-skill-2").exists()


@pytest.mark.asyncio
async def test_skill_proposal_command_handles_missing_and_usage(proposal_env):
    runner = _make_runner()
    usage = await runner._handle_skill_proposal_command(_make_event("/skill-proposal"))
    missing = await runner._handle_skill_proposal_command(_make_event("/skill-proposal show nope123"))
    assert "Usage:" in usage
    assert "not found" in missing


def _ensure_telegram_mock():
    import sys
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()
from gateway.platforms.telegram import TelegramAdapter
from gateway.config import PlatformConfig


def _make_telegram_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))
    adapter._bot = AsyncMock()
    return adapter


@pytest.mark.asyncio
async def test_telegram_send_skill_proposal_uses_inline_buttons(monkeypatch):
    adapter = _make_telegram_adapter()
    sent = {}

    class Button:
        def __init__(self, text, callback_data=None):
            self.text = text
            self.callback_data = callback_data

    class Markup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    monkeypatch.setattr("gateway.platforms.telegram.InlineKeyboardButton", Button)
    monkeypatch.setattr("gateway.platforms.telegram.InlineKeyboardMarkup", Markup)

    async def fake_send(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(message_id=99)

    adapter._send_message_with_thread_fallback = AsyncMock(side_effect=fake_send)
    result = await adapter.send_skill_proposal(
        "12345",
        {"proposal_id": "abc123", "name": "draft-skill"},
    )

    assert result.success is True
    markup = sent["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert {b.callback_data for b in buttons} == {"sp:a:abc123", "sp:r:abc123"}


@pytest.mark.asyncio
async def test_telegram_skill_proposal_callback_unauthorized(monkeypatch):
    adapter = _make_telegram_adapter()
    adapter._is_callback_user_authorized = lambda *a, **kw: False
    called = False

    def fake_approve(_proposal_id):
        nonlocal called
        called = True
        return {"success": True}

    monkeypatch.setattr("tools.skill_manager_tool._approve_skill_proposal", fake_approve)
    answers = []

    class Query:
        data = "sp:a:abc123"
        from_user = SimpleNamespace(id=42, first_name="Alice")
        message = SimpleNamespace(chat_id=12345, chat=SimpleNamespace(type="private"), message_thread_id=None)

        async def answer(self, **kwargs):
            answers.append(kwargs.get("text", ""))

        async def edit_message_text(self, **kwargs):
            raise AssertionError("unauthorized callback must not edit")

    await adapter._handle_callback_query(SimpleNamespace(callback_query=Query()), SimpleNamespace())
    assert called is False
    assert "not authorized" in answers[0]


@pytest.mark.asyncio
async def test_telegram_skill_proposal_callback_resolves_and_handles_stale(monkeypatch):
    adapter = _make_telegram_adapter()
    adapter._is_callback_user_authorized = lambda *a, **kw: True
    answers = []
    edits = []

    monkeypatch.setattr(
        "tools.skill_manager_tool._approve_skill_proposal",
        lambda proposal_id: {
            "success": proposal_id == "abc123",
            "message": "Skill proposal 'abc123' approved; skill 'draft-skill' created.",
            "error": "Skill proposal 'old123' is already approved.",
        },
    )

    class Query:
        def __init__(self, data):
            self.data = data
            self.from_user = SimpleNamespace(id=42, first_name="Alice")
            self.message = SimpleNamespace(chat_id=12345, chat=SimpleNamespace(type="private"), message_thread_id=None)

        async def answer(self, **kwargs):
            answers.append(kwargs.get("text", ""))

        async def edit_message_text(self, **kwargs):
            edits.append(kwargs)

    await adapter._handle_callback_query(SimpleNamespace(callback_query=Query("sp:a:abc123")), SimpleNamespace())
    await adapter._handle_callback_query(SimpleNamespace(callback_query=Query("sp:a:old123")), SimpleNamespace())

    assert any("approved" in a.lower() for a in answers)
    assert any("already approved" in a for a in answers)
    assert edits


@pytest.mark.asyncio
async def test_telegram_skill_proposal_reject_callback(monkeypatch):
    adapter = _make_telegram_adapter()
    adapter._is_callback_user_authorized = lambda *a, **kw: True
    answers = []
    edits = []
    seen = {}

    def fake_reject(proposal_id):
        seen["proposal_id"] = proposal_id
        return {
            "success": True,
            "message": f"Skill proposal '{proposal_id}' rejected; no live skill was created.",
        }

    monkeypatch.setattr("tools.skill_manager_tool._reject_skill_proposal", fake_reject)

    class Query:
        data = "sp:r:abc123"
        from_user = SimpleNamespace(id=42, first_name="Alice")
        message = SimpleNamespace(chat_id=12345, chat=SimpleNamespace(type="private"), message_thread_id=None)

        async def answer(self, **kwargs):
            answers.append(kwargs.get("text", ""))

        async def edit_message_text(self, **kwargs):
            edits.append(kwargs)

    await adapter._handle_callback_query(SimpleNamespace(callback_query=Query()), SimpleNamespace())

    assert seen["proposal_id"] == "abc123"
    assert any("rejected" in a.lower() for a in answers)
    assert edits
