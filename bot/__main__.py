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

from bot.metrics import setup_cost_logging
from bot.bot import main

# Инициализировать логирование AI-затрат в logs/costs/
setup_cost_logging(cost_dir="logs/costs")

if __name__ == "__main__":
    main()
