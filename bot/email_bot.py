#!/usr/bin/env python3
"""Email bot entry point — IMAP polling + SMTP replies."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from bot.bot import init_agents, shutdown_agents  # noqa: E402
from bot.config import build_global_config, get_settings, load_settings  # noqa: E402
from bot.email.transport import EmailTransport  # noqa: E402
from bot.logging_config import setup_logging  # noqa: E402
from bot.messages import InboundMessage, OutboundMessage  # noqa: E402
from bot.metrics import setup_cost_logging, setup_provider_response_logging  # noqa: E402
from bot_lib.exceptions import AIError, ODataError, ODataSkillError  # noqa: E402

log = logging.getLogger(__name__)


async def handle_inbound(inbound: InboundMessage) -> OutboundMessage | None:
    """Обработать входящее email через ChatManager."""
    from bot.bot import _chat_mgr as chat_mgr

    if not chat_mgr:
        log.error("ChatManager не инициализирован")
        return OutboundMessage(
            text="⚠️ Внутренняя ошибка: бот не готов.",
            channel=inbound.channel,
        )

    chat = chat_mgr.get_or_create(inbound.conversation_id)

    try:
        return await chat.process_inbound(inbound)
    except ODataError as e:
        log.error("OData error: %s", e)
        return OutboundMessage(text=f"⚠️ Ошибка OData: {e}", channel=inbound.channel)
    except AIError as e:
        log.error("AI error: %s", e)
        return OutboundMessage(text=f"⚠️ Ошибка AI: {e}", channel=inbound.channel)
    except ODataSkillError as e:
        log.error("Internal error: %s", e)
        return OutboundMessage(text=f"⚠️ Внутренняя ошибка: {e}", channel=inbound.channel)
    except Exception as e:
        log.exception("Unexpected error processing email")
        return OutboundMessage(text=f"⚠️ Непредвиденная ошибка: {e}", channel=inbound.channel)


async def run_email_bot(env_file: str, profile: str, cache_dir: str) -> None:
    """Запустить email-транспорт."""
    settings = get_settings()
    email_cfg = settings.email

    if not email_cfg.enabled:
        log.error("Email-транспорт отключён (email.enabled=false)")
        return

    if not email_cfg.imap_host or not email_cfg.smtp_host:
        log.error("Не заданы imap_host / smtp_host в конфигурации email")
        return

    profile_cfg: dict[str, Any] = {
        "agents": settings.agents_config,
        "formatter": settings.formatter.model_dump(),
        **build_global_config(settings),
    }
    await init_agents(profile_cfg, cache_dir, env_file)

    transport = EmailTransport(
        settings=email_cfg,
        cache_dir=cache_dir,
        on_message=handle_inbound,
    )

    try:
        await transport.run_forever()
    finally:
        transport.stop()
        await shutdown_agents()


def main() -> None:
    _root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="1С OData Email Bot")
    parser.add_argument("--env-file", default=str(_root / "env.json"))
    parser.add_argument("--profile", default="default")
    parser.add_argument("--cache-dir", default=str(_root / ".cache"))
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument("--log-file", default=None)
    args = parser.parse_args()

    settings = load_settings(env_file=args.env_file, profile=args.profile)
    log_level = args.log_level or settings.log_level or "INFO"
    setup_logging(level=log_level, log_file=args.log_file)
    setup_cost_logging(cost_dir="logs/costs")
    setup_provider_response_logging(log_dir="logs")

    asyncio.run(run_email_bot(args.env_file, args.profile, args.cache_dir))


if __name__ == "__main__":
    main()
