"""L3: E2E email-тесты — SMTP → бот → IMAP, живой AI + 1С.

Требования:
  - docker compose -f docker-compose.test.yml up -d  (GreenMail)
  - env.test.json (скопировать из env.test.example.json)
  - AI_API_KEY и ODATA_URL (или в env.test.json / secrets)

Запуск:
  pytest -m "slow" tests/integration/test_email_e2e.py -v
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import pytest

from bot.config import load_settings
from bot.email.transport import EmailTransport
from bot.email_bot import handle_inbound
from tests.helpers.email_harness import (
    ImapConfig,
    SmtpConfig,
    assert_no_error,
    assert_reply_in_thread,
    body_text,
    imap_max_uid,
    save_artifact,
    send_email,
    unique_subject,
    wait_for_reply,
)
from tests.helpers.scenario_catalog import Scenario, scenarios_by_layer

log = logging.getLogger(__name__)

BOT_ADDR = "bot@local.test"
TESTER_ADDR = "tester@local.test"
E2E_TIMEOUT = float(os.environ.get("E2E_EMAIL_TIMEOUT", "600"))


def _resolve_env_file(env_test_file: str | None) -> str:
    if env_test_file:
        return env_test_file
    pytest.skip("env.test.json not found (copy env.test.example.json)")


def _apply_env_overrides(env_file: str) -> None:
    """Подставить секреты из переменных окружения в уже загруженный профиль."""
    from bot.config import get_settings

    settings = get_settings()
    ai_key = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if ai_key:
        settings.ai.api_key = ai_key  # type: ignore[attr-defined]

    odata_url = os.environ.get("ODATA_URL")
    if odata_url and "odata" in settings.agents_config:
        settings.agents_config["odata"]["odata_url"] = odata_url
        mcp = settings.agents_config["odata"].get("mcp_servers", {}).get("odata", {})
        if mcp.get("env"):
            mcp["env"]["ODATA_URL"] = odata_url


@pytest.fixture
async def e2e_bot_runtime(
    env_test_file: str | None,
    require_live_ai,
    require_odata_url,
    require_mail_server,
    tmp_path,
):
    """Запустить EmailTransport в фоне с реальными агентами."""
    env_file = _resolve_env_file(env_test_file)
    load_settings(env_file=env_file, profile="default")
    _apply_env_overrides(env_file)

    from bot.bot import init_agents, shutdown_agents
    from bot.config import build_global_config, get_settings

    settings = get_settings()
    cache_dir = str(tmp_path / "cache")
    profile_cfg: dict[str, Any] = {
        "agents": settings.agents_config,
        "formatter": settings.formatter.model_dump(),
        **build_global_config(settings),
    }
    await init_agents(profile_cfg, cache_dir, env_file)

    transport = EmailTransport(
        settings=settings.email,
        cache_dir=cache_dir,
        on_message=handle_inbound,
    )

    task = asyncio.create_task(transport.run_forever())

    yield transport, settings

    transport.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await shutdown_agents()


def _smtp_from_settings(settings) -> SmtpConfig:
    e = settings.email
    return SmtpConfig(
        host=os.environ.get("TEST_SMTP_HOST", e.smtp_host),
        port=int(os.environ.get("TEST_SMTP_PORT", str(e.smtp_port))),
        user=os.environ.get("TEST_SMTP_USER", ""),
        password=os.environ.get("TEST_SMTP_PASSWORD", ""),
        use_ssl=e.smtp_use_ssl,
        use_tls=e.smtp_use_tls,
    )


def _imap_tester(settings) -> ImapConfig:
    e = settings.email
    return ImapConfig(
        host=os.environ.get("TEST_IMAP_HOST", e.imap_host),
        port=int(os.environ.get("TEST_IMAP_PORT", str(e.imap_port))),
        user=os.environ.get("TEST_IMAP_USER", "tester"),
        password=os.environ.get("TEST_IMAP_PASSWORD", "secret"),
        use_ssl=e.imap_use_ssl,
    )


async def _run_email_roundtrip(
    settings,
    *,
    subject: str,
    body: str,
    from_addr: str = TESTER_ADDR,
    in_reply_to: str = "",
    references: str = "",
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> tuple[str, Any]:
    """Отправить письмо боту и дождаться ответа на ящике tester."""
    smtp = _smtp_from_settings(settings)
    imap = _imap_tester(settings)

    since_uid = await asyncio.to_thread(imap_max_uid, imap)

    msg_id = send_email(
        smtp=smtp,
        to_addr=BOT_ADDR,
        subject=subject,
        body=body,
        from_addr=from_addr,
        in_reply_to=in_reply_to,
        references=references,
        attachments=attachments,
    )

    reply = await asyncio.to_thread(
        wait_for_reply,
        subject_contains=subject,
        timeout=E2E_TIMEOUT,
        poll_interval=settings.email.poll_interval,
        imap=imap,
        from_contains="bot@local.test",
        since_uid=since_uid,
    )
    return msg_id, reply


def _check_asserts(scenario: Scenario, reply, original_msg_id: str) -> None:
    text = body_text(reply)
    for name in scenario.asserts:
        if name == "no_error":
            assert_no_error(text)
        elif name == "no_stack_trace":
            assert "traceback" not in text.lower()
        elif name == "polite_error":
            assert len(text.strip()) > 10
        elif name == "has_table_or_list":
            assert re.search(r"сотрудник|таблиц|<table|список|\d", text, re.I)
        elif name == "has_number":
            assert re.search(r"\d+", text)
        elif name == "reply_in_thread":
            assert_reply_in_thread(reply, original_msg_id)
        elif name == "uses_context":
            assert len(text.strip()) > 5
        elif name == "no_reply":
            raise AssertionError("Expected no reply but got one")
        elif name == "has_attachment_or_long_body":
            assert reply.attachments or len(text) > 200
        elif name == "has_png_attachment":
            assert any(a[0].endswith(".png") for a in reply.attachments)
        elif name == "mentions_attachment":
            assert "влож" in text.lower() or "csv" in text.lower() or "data" in text.lower()
        elif name == "polite_response":
            assert len(text.strip()) > 5
        elif name == "human_readable_labels":
            assert len(text) > 20


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_list_employees(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    subject = unique_subject("employees")
    msg_id, reply = await _run_email_roundtrip(
        settings,
        subject=subject,
        body="Покажи 5 сотрудников из справочника",
    )
    try:
        _check_asserts(
            Scenario(
                id="email-list-employees",
                layer="e2e",
                question="",
                asserts=["no_error", "has_table_or_list", "reply_in_thread"],
            ),
            reply,
            msg_id,
        )
    except AssertionError:
        save_artifact(subject, reply.raw)
        raise


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_count_employees(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    subject = unique_subject("count")
    msg_id, reply = await _run_email_roundtrip(
        settings,
        subject=subject,
        body="Сколько записей в справочнике сотрудников?",
    )
    try:
        _check_asserts(
            Scenario(id="email-count-employees", layer="e2e", question="", asserts=["no_error", "has_number"]),
            reply,
            msg_id,
        )
    except AssertionError:
        save_artifact(subject, reply.raw)
        raise


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_unknown_entity(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    subject = unique_subject("unknown")
    msg_id, reply = await _run_email_roundtrip(
        settings,
        subject=subject,
        body="Покажи данные из Catalog_НесуществующийОбъект",
    )
    try:
        _check_asserts(
            Scenario(
                id="email-unknown-entity",
                layer="e2e",
                question="",
                asserts=["no_stack_trace", "polite_error"],
            ),
            reply,
            msg_id,
        )
    except AssertionError:
        save_artifact(subject, reply.raw)
        raise


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_e2e_thread_followup(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    subject = unique_subject("thread")
    first_id, first_reply = await _run_email_roundtrip(
        settings,
        subject=subject,
        body="Покажи 10 сотрудников",
    )
    assert_no_error(body_text(first_reply))

    msg_id, reply = await _run_email_roundtrip(
        settings,
        subject=f"Re: {subject}",
        body="А теперь только первые 3",
        in_reply_to=first_id,
        references=first_id,
    )
    try:
        _check_asserts(
            Scenario(
                id="email-thread-followup",
                layer="e2e",
                question="",
                asserts=["no_error", "reply_in_thread", "uses_context"],
            ),
            reply,
            msg_id,
        )
    except AssertionError:
        save_artifact(f"{subject}-followup", reply.raw)
        raise


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_e2e_disallowed_sender_no_reply(e2e_bot_runtime):
    """Письмо от неразрешённого отправителя не должно получить ответ."""
    _, settings = e2e_bot_runtime
    subject = unique_subject("blocked")
    smtp = _smtp_from_settings(settings)
    imap = _imap_tester(settings)

    send_email(
        smtp=smtp,
        to_addr=BOT_ADDR,
        subject=subject,
        body="Покажи сотрудников",
        from_addr="stranger@blocked.test",
    )

    with pytest.raises(TimeoutError):
        await asyncio.to_thread(
            wait_for_reply,
            subject_contains=subject,
            timeout=15.0,
            poll_interval=2.0,
            imap=imap,
            from_contains="bot@local.test",
        )


@pytest.mark.slow
@pytest.mark.integration
def test_catalog_implemented_e2e_ids_exist():
    """Каждый implemented e2e-сценарий имеет тест или явную пометку planned."""
    implemented = {s.id for s in scenarios_by_layer("e2e", status="implemented")}
    covered = {
        "email-list-employees",
        "email-count-employees",
        "email-unknown-entity",
        "email-thread-followup",
        "email-disallowed-sender",
    }
    missing = implemented - covered
    assert not missing, f"Add E2E tests for catalog ids: {missing}"


@pytest.mark.integration
def test_catalog_yaml_loads():
    """Каталог сценариев читается и содержит минимум 15 записей."""
    from tests.helpers.scenario_catalog import load_catalog

    items = load_catalog()
    assert len(items) >= 15
    layers = {s.layer for s in items}
    assert "e2e" in layers
    assert "l1" in layers
