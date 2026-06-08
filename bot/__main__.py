#!/usr/bin/env python3
"""Entry point: python -m bot [--transport telegram|email|both]"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from bot.logging_config import setup_logging

setup_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    log_dir="logs",
)

from bot.agents.odata.parse_failure import setup_parse_failure_journal  # noqa: E402
from bot.metrics import setup_cost_logging, setup_provider_response_logging  # noqa: E402
from bot.response_error_journal import setup_error_response_journal  # noqa: E402

setup_cost_logging(cost_dir="logs/costs")
setup_provider_response_logging(log_dir="logs")
setup_error_response_journal(log_dir="logs")
setup_parse_failure_journal(log_dir="logs")


def main() -> None:
    parser = argparse.ArgumentParser(description="1С OData Bot")
    parser.add_argument(
        "--transport",
        default="telegram",
        choices=["telegram", "email", "both"],
        help="Канал доставки: telegram, email или оба одновременно",
    )
    args, remaining = parser.parse_known_args()

    if args.transport == "telegram":
        from bot.bot import main as telegram_main

        sys.argv = [sys.argv[0], *remaining]
        telegram_main()
    elif args.transport == "email":
        from bot.email_bot import main as email_main

        sys.argv = [sys.argv[0], *remaining]
        email_main()
    elif args.transport == "both":
        _run_both(remaining)


def _run_both(remaining: list[str]) -> None:
    """Запустить Telegram и Email параллельно."""
    from bot.bot import main as telegram_main
    from bot.config import build_global_config, get_settings, load_settings

    _root = Path(__file__).resolve().parent.parent
    env_file = str(_root / "env.json")
    profile = "default"
    cache_dir = str(_root / ".cache")

    for i, arg in enumerate(remaining):
        if arg == "--env-file" and i + 1 < len(remaining):
            env_file = remaining[i + 1]
        elif arg == "--profile" and i + 1 < len(remaining):
            profile = remaining[i + 1]
        elif arg == "--cache-dir" and i + 1 < len(remaining):
            cache_dir = remaining[i + 1]

    load_settings(env_file, profile)
    settings = get_settings()

    async def _combined() -> None:
        from bot.bot import init_agents, shutdown_agents
        from bot.email.transport import EmailTransport
        from bot.email_bot import handle_inbound

        profile_cfg = {
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

        email_task = asyncio.create_task(transport.run_forever())

        # Telegram запускается в отдельном потоке (blocking polling)
        import threading

        def _telegram_thread() -> None:
            sys.argv = [sys.argv[0], *remaining]
            telegram_main()

        tg_thread = threading.Thread(target=_telegram_thread, daemon=True)
        tg_thread.start()

        try:
            await email_task
        finally:
            transport.stop()
            await shutdown_agents()

    asyncio.run(_combined())


if __name__ == "__main__":
    main()
