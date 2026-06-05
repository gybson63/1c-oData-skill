"""L1: интеграционные тесты email-ветки Chat.process_inbound."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.chat import Chat
from bot.config import load_settings
from bot.history import HistoryManager
from bot.messages import AgentProcessResult, Attachment, InboundMessage, TransportChannel, email_conversation_id
from bot.metrics import session_tokens


def _email_inbound(
    text: str = "Покажи сотрудников",
    subject: str = "Запрос",
    thread_id: str = "<thread-1@test>",
) -> InboundMessage:
    return InboundMessage(
        conversation_id=email_conversation_id(thread_id),
        channel=TransportChannel.EMAIL,
        text=text,
        sender="tester@local.test",
        metadata={"subject": subject, "thread_id": thread_id},
    )


def _pagination_history(entity: str = "Catalog_Сотрудники") -> list[dict[str, str]]:
    ctx = {
        "entity": entity,
        "total": 100,
        "skip": 0,
        "shown": 20,
        "top": 20,
    }
    return [
        {"role": "user", "content": "список"},
        {"role": "assistant", "content": json.dumps(ctx, ensure_ascii=False)},
    ]


@pytest.fixture
def email_chat(email_test_env_json: str):
    """Chat с мок-агентом и мок-форматтером, settings загружены."""
    load_settings(env_file=email_test_env_json, profile="default")
    agent = MagicMock()
    agent.process_message = AsyncMock(
        return_value=AgentProcessResult(
            text="Короткий ответ агента",
            history=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "Короткий ответ агента"}],
        )
    )
    agent.execute_all_pages_with_ctx = AsyncMock(return_value="Полный список из 100 записей")

    formatter = MagicMock()
    formatter.is_initialized = True
    formatter.format_response = AsyncMock(side_effect=lambda answer, **_: f"<p>{answer}</p>")

    chat = Chat(
        chat_id=-12345,
        agent=agent,
        formatter=formatter,
        history_mgr=HistoryManager(persist_dir=None),
    )
    return chat, agent, formatter


@pytest.mark.integration
@pytest.mark.asyncio
async def test_short_answer_inline(email_chat):
    chat, agent, formatter = email_chat
    inbound = _email_inbound()

    outbound = await chat.process_inbound(inbound)

    agent.process_message.assert_awaited_once()
    formatter.format_response.assert_awaited()
    assert outbound.channel == TransportChannel.EMAIL
    assert outbound.attachments == []
    assert "Короткий ответ" in outbound.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_long_answer_attachment(email_chat):
    chat, agent, _ = email_chat
    long_text = "A" * 500
    agent.process_message.return_value = AgentProcessResult(
        text=long_text,
        history=[{"role": "assistant", "content": long_text}],
    )

    outbound = await chat.process_inbound(_email_inbound())

    assert len(outbound.attachments) >= 1
    assert outbound.attachments[0].filename.endswith(".html")
    assert "вложении" in outbound.text.lower() or "Краткий просмотр" in outbound.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pagination_triggers_fetch_all(email_chat):
    chat, agent, formatter = email_chat
    agent.process_message.return_value = AgentProcessResult(
        text="Страница 1",
        history=_pagination_history(),
    )

    outbound = await chat.process_inbound(_email_inbound())

    agent.execute_all_pages_with_ctx.assert_awaited_once()
    assert formatter.format_response.await_count >= 2
    assert outbound.channel == TransportChannel.EMAIL
    assert outbound.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_analytics_attachments_and_chart_html(email_chat):
    chat, agent, _ = email_chat
    png = Attachment(filename="chart.png", content_type="image/png", data=b"\x89PNG")
    agent.process_message.return_value = AgentProcessResult(
        text="График готов",
        history=[{"role": "assistant", "content": "График готов"}],
        attachments=[png],
        chart_html="<html><body>plot</body></html>",
    )

    outbound = await chat.process_inbound(_email_inbound())

    filenames = [a.filename for a in outbound.attachments]
    assert "chart.png" in filenames
    assert "chart.html" in filenames


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skip_formatter_raw_text(email_chat):
    chat, agent, formatter = email_chat
    raw = "Y" * 300
    agent.process_message.return_value = AgentProcessResult(
        text=raw,
        history=[{"role": "assistant", "content": raw}],
        skip_formatter=True,
    )

    outbound = await chat.process_inbound(_email_inbound())

    formatter.format_response.assert_not_awaited()
    assert len(outbound.attachments) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_body_polite_response(email_chat):
    chat, agent, formatter = email_chat
    agent.process_message.return_value = AgentProcessResult(
        text="Пожалуйста, опишите ваш запрос.",
        history=[
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "Пожалуйста, опишите ваш запрос."},
        ],
    )

    outbound = await chat.process_inbound(_email_inbound(text=""))

    agent.process_message.assert_awaited_once()
    formatter.format_response.assert_awaited()
    assert len(outbound.text.strip()) > 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_token_footer_in_attachment_when_long(email_chat):
    chat, agent, _ = email_chat
    inbound = _email_inbound()
    chat.chat_id = inbound.chat_id
    chat_id = inbound.chat_id
    session_tokens.record(chat_id, input_tokens=50, output_tokens=30, cost_usd=0.001)

    long_text = "Z" * 400
    agent.process_message.return_value = AgentProcessResult(
        text=long_text,
        history=[{"role": "assistant", "content": long_text}],
    )

    outbound = await chat.process_inbound(inbound)

    assert outbound.attachments
    footer = session_tokens.get(chat_id).format_compact()
    assert footer.encode() in outbound.attachments[0].data
    session_tokens.clear(chat_id)
