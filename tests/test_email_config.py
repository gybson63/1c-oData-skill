"""Тесты загрузки email-настроек."""

import json

from bot.config import EmailSettings, load_settings


def test_email_settings_defaults():
    email = EmailSettings()
    assert email.enabled is False
    assert email.imap_port == 993
    assert email.context_max_chars == 12000
    assert email.inline_max_chars == 8000
    assert email.max_fetch_records == 500


def test_load_email_settings(tmp_path):
    data = {
        "profiles": {
            "default": {
                "telegram_token": "TOKEN",
                "ai_api_key": "KEY",
                "email": {
                    "enabled": True,
                    "imap_host": "imap.test.com",
                    "smtp_host": "smtp.test.com",
                    "imap_user": "bot@test.com",
                    "context_max_chars": 8000,
                },
            }
        }
    }
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps(data), encoding="utf-8")

    settings = load_settings(str(env_file), "default")
    assert settings.email.enabled is True
    assert settings.email.imap_host == "imap.test.com"
    assert settings.email.context_max_chars == 8000
