#!/usr/bin/env python3
"""Entry point: python -m bot"""

import os

from bot.logging_config import setup_logging

# Настроить логирование до импорта остальных модулей,
# чтобы логгеры в поддулях работали корректно.
setup_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    log_dir="logs",
)

from bot.bot import main  # noqa: E402
from bot.metrics import setup_cost_logging, setup_provider_response_logging  # noqa: E402

# Инициализировать логирование AI-затрат в logs/costs/
setup_cost_logging(cost_dir="logs/costs")

# Инициализировать сохранение ответов провайдера в logs/<session>/
setup_provider_response_logging(log_dir="logs")

if __name__ == "__main__":
    main()