#!/usr/bin/env python3
"""Entry point: python -m bot"""

from bot.logging_config import setup_logging

# Настроить логирование до импорта остальных модулей,
# чтобы логгеры в поддулях работали корректно.
setup_logging()

from bot.bot import main

if __name__ == "__main__":
    main()