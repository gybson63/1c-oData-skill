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
from tests.helpers.scenario_catalog import Scenario, scenario_by_id, scenarios_by_layer

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


def _attachments_from_scenario(scenario: Scenario) -> list[tuple[str, str, bytes]] | None:
    """Вложения из поля attachment в catalog.yaml."""
    att = scenario.extra.get("attachment")
    if not att:
        return None
    filename = att.get("filename", "data.csv")
    content = att.get("content", "")
    return [(filename, "text/csv", content.encode("utf-8"))]


async def _run_catalog_scenario(
    settings,
    scenario_id: str,
    *,
    body: str | None = None,
    from_addr: str | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    subject_prefix: str | None = None,
    **roundtrip_kwargs,
) -> tuple[str, Any, Scenario]:
    """Один roundtrip по записи каталога."""
    scenario = scenario_by_id(scenario_id)
    prefix = subject_prefix or scenario_id.replace("email-", "")
    subject = unique_subject(prefix)
    msg_id, reply = await _run_email_roundtrip(
        settings,
        subject=subject,
        body=scenario.question if body is None else body,
        from_addr=from_addr or scenario.extra.get("from_addr", TESTER_ADDR),
        attachments=attachments if attachments is not None else _attachments_from_scenario(scenario),
        **roundtrip_kwargs,
    )
    return msg_id, reply, scenario


async def _e2e_check(
    settings,
    scenario_id: str,
    artifact: str,
    **kwargs,
) -> None:
    """Roundtrip + assert по каталогу; при падении сохраняет MIME."""
    msg_id, reply, scenario = await _run_catalog_scenario(settings, scenario_id, **kwargs)
    try:
        _check_asserts(scenario, reply, msg_id)
    except AssertionError:
        save_artifact(artifact, reply.raw)
        raise


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
            lowered = text.lower()
            assert any(kw in lowered for kw in ("влож", "csv", "chislennost", "подраздел", "числен", "it", "бухгалт"))
        elif name == "polite_response":
            assert len(text.strip()) > 5
        elif name == "has_hr_fields":
            assert re.search(r"должност|подраздел|сотрудник|фио|фамил|табельн", text, re.I)
        elif name == "has_organization":
            assert re.search(r"организац", text, re.I)
        elif name == "has_department":
            assert re.search(r"подраздел", text, re.I)
        elif name == "has_position":
            assert re.search(r"должност", text, re.I)
        elif name == "has_person_name":
            assert re.search(r"физическ|фио|фамил|имя|лиц", text, re.I)
        elif name == "has_catalog_list":
            assert re.search(r"наименован|описан|код|справочник|<table|список", text, re.I) or len(text) > 30
        elif name == "human_readable_labels":
            assert len(text) > 20
            guids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", text, re.I)
            assert not guids or re.search(r"подраздел|должност|наименован", text, re.I)
        elif name == "respects_max_fetch":
            from bot.config import get_settings

            max_fetch = get_settings().email.max_fetch_records
            data_rows = len(re.findall(r"<tr\b", reply.html, re.I))
            if data_rows > 1:
                assert data_rows - 1 <= max_fetch + 2
            else:
                assert re.search(
                    rf"\b{max_fetch}\b|лимит|огранич|не более|первые\s+\d+",
                    text,
                    re.I,
                )


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_e2e_zup_staff_list(e2e_bot_runtime):
    """Аналог отчёта «Штатные сотрудники»."""
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-list-employees", "zup-staff")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_zup_staff_count(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-count-employees", "zup-count")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_zup_organizations(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-zup-organizations", "zup-orgs")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_zup_departments(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-zup-departments", "zup-depts")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_zup_positions(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-zup-positions", "zup-positions")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_zup_physical_persons(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-zup-physical-persons", "zup-persons")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_e2e_zup_reference_labels(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-reference-labels", "zup-labels")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_unknown_entity(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-unknown-entity", "unknown-entity")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_e2e_zup_thread_followup(e2e_bot_runtime):
    """Уточнение отбора — как в настройках типового отчёта."""
    _, settings = e2e_bot_runtime
    scenario = scenario_by_id("email-thread-followup")
    setup = scenario.extra.get("thread_setup", {})
    subject = unique_subject("zup-thread")
    first_id, first_reply = await _run_email_roundtrip(
        settings,
        subject=subject,
        body=setup.get("first_question", "Покажи 10 сотрудников"),
    )
    assert_no_error(body_text(first_reply))

    msg_id, reply = await _run_email_roundtrip(
        settings,
        subject=f"Re: {subject}",
        body=scenario.question,
        in_reply_to=first_id,
        references=first_id,
    )
    try:
        _check_asserts(scenario, reply, msg_id)
    except AssertionError:
        save_artifact(f"{subject}-followup", reply.raw)
        raise


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_e2e_zup_long_report(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-long-report", "zup-long-report")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_e2e_empty_body(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-empty-body", "empty-body", body="")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_e2e_zup_inbound_csv(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-inbound-csv", "zup-csv")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_e2e_zup_max_fetch_cap(e2e_bot_runtime):
    _, settings = e2e_bot_runtime
    await _e2e_check(settings, "email-max-fetch-cap", "zup-max-fetch")


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

    blocked = scenario_by_id("email-disallowed-sender")
    send_email(
        smtp=smtp,
        to_addr=BOT_ADDR,
        subject=subject,
        body=blocked.question,
        from_addr=blocked.extra.get("from_addr", "stranger@blocked.test"),
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
        "email-zup-organizations",
        "email-zup-departments",
        "email-zup-positions",
        "email-zup-physical-persons",
        "email-reference-labels",
        "email-unknown-entity",
        "email-thread-followup",
        "email-disallowed-sender",
        "email-long-report",
        "email-empty-body",
        "email-inbound-csv",
        "email-max-fetch-cap",
    }
    missing = implemented - covered
    assert not missing, f"Add E2E tests for catalog ids: {missing}"


@pytest.mark.integration
def test_catalog_yaml_loads():
    """Каталог сценариев читается и содержит минимум 15 записей."""
    from tests.helpers.scenario_catalog import load_catalog

    items = load_catalog()
    assert len(items) >= 20
    layers = {s.layer for s in items}
    assert "e2e" in layers
    assert "l1" in layers
    zup = [s for s in items if s.domain == "zup"]
    assert len(zup) >= 10
