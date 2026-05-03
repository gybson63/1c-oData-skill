"""Тесты конфигурации (bot.config)."""

import json
import pytest

from bot.config import (
    AppSettings,
    AISettings,
    BotSettings,
    FormatterSettings,
    ODataQuerySettings,
    TelegramTransportSettings,
    build_global_config,
    get_settings,
    load_settings,
)
from bot_lib.exceptions import ConfigError


# =========================================================================
# load_settings — успешные сценарии
# =========================================================================


class TestLoadSettings:
    """Тесты загрузки конфигурации из env.json."""

    def test_load_default_profile(self, sample_env_json: str):
        """Загрузка профиля 'default'."""
        settings = load_settings(sample_env_json, "default")
        assert isinstance(settings, AppSettings)

    def test_ai_settings(self, sample_env_json: str):
        """Настройки AI корректно разобраны."""
        load_settings(sample_env_json, "default")
        settings = get_settings()
        assert settings.ai.api_key == "sk-test-key-12345"
        assert settings.ai.model == "gpt-4o-mini"
        assert settings.ai.base_url == "https://api.openai.com/v1"
        assert settings.ai.rpm == 20
        assert settings.ai.temperature == 0.1
        assert settings.ai.temperature_step2 == 0.3

    def test_bot_settings(self, sample_env_json: str):
        """Настройки бота."""
        load_settings(sample_env_json, "default")
        settings = get_settings()
        assert settings.bot.token == "1234567890:FAKE_TOKEN_FOR_TESTS"

    def test_telegram_settings(self, sample_env_json: str):
        """Настройки транспорта Telegram."""
        load_settings(sample_env_json, "default")
        settings = get_settings()
        assert settings.telegram.message_max_length == 4000
        assert settings.telegram.connect_timeout == 30
        assert settings.telegram.read_timeout == 120

    def test_odata_query_settings(self, sample_env_json: str):
        """Настройки OData-запросов."""
        load_settings(sample_env_json, "default")
        settings = get_settings()
        assert settings.odata_query.default_top == 20
        assert settings.odata_query.max_top == 50
        assert settings.odata_query.request_timeout == 60

    def test_formatter_settings(self, sample_env_json: str):
        """Настройки форматтера."""
        load_settings(sample_env_json, "default")
        settings = get_settings()
        assert settings.formatter.enabled is True
        assert settings.formatter.formatter_model == "gpt-4o-mini"
        assert settings.formatter.temperature == 0.2

    def test_general_settings(self, sample_env_json: str):
        """Общие настройки."""
        load_settings(sample_env_json, "default")
        settings = get_settings()
        assert settings.history_max_turns == 10


# =========================================================================
# load_settings — ошибки
# =========================================================================


class TestLoadSettingsErrors:
    """Тесты обработки ошибок загрузки."""

    def test_missing_file_raises_config_error(self, tmp_path):
        """Файл не найден → ConfigError."""
        with pytest.raises(ConfigError, match="не найдена"):
            load_settings(str(tmp_path / "nonexistent.json"))

    def test_invalid_json_raises_config_error(self, tmp_path):
        """Невалидный JSON → ConfigError."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(ConfigError, match="Ошибка чтения"):
            load_settings(str(bad_file))

    def test_missing_profile_raises_value_error(self, sample_env_json: str):
        """Профиль не найден → ValueError."""
        with pytest.raises(ValueError, match="не найден"):
            load_settings(sample_env_json, "nonexistent_profile")

    def test_get_settings_without_load_raises_runtime(self):
        """get_settings() без load_settings() → RuntimeError."""
        # Сбрасываем singleton
        import bot.config as cfg
        cfg._settings = None

        with pytest.raises(RuntimeError, match="load_settings"):
            get_settings()


# =========================================================================
# Legacy-формат (без "profiles")
# =========================================================================


class TestLegacyFormat:
    """Тесты поддержки старого формата env.json (без секции 'profiles')."""

    def test_load_old_format(self, tmp_path):
        """Старый формат {"default": {...}} загружается."""
        data = {
            "default": {
                "telegram_token": "TOKEN",
                "ai_api_key": "KEY",
            }
        }
        env_file = tmp_path / "env.json"
        env_file.write_text(json.dumps(data), encoding="utf-8")

        settings = load_settings(str(env_file), "default")
        assert settings.bot.token == "TOKEN"
        assert settings.ai.api_key == "KEY"


# =========================================================================
# build_global_config
# =========================================================================


class TestBuildGlobalConfig:
    """Тесты сборки global_config для backward compatibility."""

    def test_build_global_config(self, sample_env_json: str):
        """global_config содержит ожидаемые ключи."""
        settings = load_settings(sample_env_json, "default")
        gc = build_global_config(settings)

        assert gc["ai_api_key"] == "sk-test-key-12345"
        assert gc["ai_model"] == "gpt-4o-mini"
        assert gc["ai_base_url"] == "https://api.openai.com/v1"
        assert gc["ai_rpm"] == 20
        assert gc["ai_temperature"] == 0.1
        assert gc["ai_temperature_step2"] == 0.3
        assert gc["history_max_turns"] == 10


# =========================================================================
# Pydantic model defaults
# =========================================================================


class TestModelDefaults:
    """Тесты значений по умолчанию для Pydantic-моделей."""

    def test_ai_defaults(self):
        ai = AISettings()
        assert ai.api_key == ""
        assert ai.base_url is None
        assert ai.model == "gpt-4o-mini"
        assert ai.rpm == 20
        assert ai.temperature == 0.1
        assert ai.temperature_step2 == 0.3

    def test_bot_defaults(self):
        bot = BotSettings()
        assert bot.token == ""

    def test_odata_query_defaults(self):
        odata = ODataQuerySettings()
        assert odata.default_top == 20
        assert odata.max_top == 50
        assert odata.request_timeout == 60
        assert odata.metadata_cache_seconds == 86400

    def test_telegram_defaults(self):
        tg = TelegramTransportSettings()
        assert tg.message_max_length == 4000
        assert tg.retry_count == 2

    def test_formatter_defaults(self):
        fmt = FormatterSettings()
        assert fmt.enabled is True
        assert fmt.formatter_model == "gpt-4o-mini"
        assert fmt.temperature == 0.2

    def test_app_settings_defaults(self):
        app = AppSettings()
        assert app.cache_dir == ".cache"
        assert app.log_level == "INFO"
        assert app.log_file is None
        assert app.history_max_turns == 10