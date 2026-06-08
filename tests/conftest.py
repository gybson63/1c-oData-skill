"""Общие фикстуры для тестов."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from tests.helpers.email_harness import ImapConfig, SmtpConfig


@pytest.fixture
def sample_metadata_xml() -> str:
    """Минимальный $metadata XML для тестов.

    Содержит:
    - Namespace «TestConfig»
    - EntityContainer с Catalog_Сотрудники и Document_Увольнение
    - EntityType Catalog_Сотрудники с Property: Description, Code, Ref_Key
    - EntityType Document_Увольнение с Property: Number, Date, Ref_Key
      и NavigationProperty: Сотрудник
    """
    return """\
<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0">
  <edmx:DataServices xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
    <Schema Namespace="TestConfig" xmlns="http://schemas.microsoft.com/ado/2009/11/edm">
      <EntityType Name="Catalog_Сотрудники">
        <Key>
          <PropertyRef Name="Ref_Key"/>
        </Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Description" Type="Edm.String"/>
        <Property Name="Code" Type="Edm.String" MaxLength="9"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
      </EntityType>
      <EntityType Name="Document_Увольнение">
        <Key>
          <PropertyRef Name="Ref_Key"/>
        </Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Number" Type="Edm.String"/>
        <Property Name="Date" Type="Edm.DateTimeOffset"/>
        <Property Name="Posted" Type="Edm.Boolean" Nullable="false"/>
        <NavigationProperty Name="Сотрудник" Type="TestConfig.Catalog_Сотрудники"/>
      </EntityType>
      <EntityContainer Name="TestConfig">
        <EntitySet Name="Catalog_Сотрудники" EntityType="TestConfig.Catalog_Сотрудники"/>
        <EntitySet Name="Document_Увольнение" EntityType="TestConfig.Document_Увольнение"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""


@pytest.fixture
def odata_url() -> str:
    """Базовый URL OData для тестов."""
    return "http://localhost/odata/standard.1c"


@pytest.fixture
def sample_env_json(tmp_path) -> str:
    """Создать тестовый env.json и вернуть путь к нему."""
    import json

    data = {
        "profiles": {
            "default": {
                "telegram_token": "1234567890:FAKE_TOKEN_FOR_TESTS",
                "ai_api_key": "sk-test-key-12345",
                "ai_base_url": "https://api.openai.com/v1",
                "ai_model": "gpt-4o-mini",
                "ai_rpm": 20,
                "ai_temperature": 0.1,
                "ai_temperature_step2": 0.3,
                "history_max_turns": 10,
                "telegram": {
                    "message_max_length": 4000,
                    "connect_timeout": 30,
                    "read_timeout": 120,
                    "write_timeout": 60,
                    "retry_count": 2,
                    "retry_delay": 2,
                    "polling_restart_delay": 5,
                },
                "odata": {
                    "default_top": 20,
                    "max_top": 50,
                    "request_timeout": 60,
                },
                "agents": {},
                "formatter": {
                    "enabled": True,
                    "formatter_model": "gpt-4o-mini",
                    "temperature": 0.2,
                },
            }
        }
    }

    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(env_file)


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def mail_smtp() -> SmtpConfig:
    """SMTP-конфиг тестового почтового стенда (GreenMail)."""
    return SmtpConfig(
        host=os.environ.get("TEST_SMTP_HOST", "localhost"),
        port=int(os.environ.get("TEST_SMTP_PORT", "3025")),
        user=os.environ.get("TEST_SMTP_USER", ""),
        password=os.environ.get("TEST_SMTP_PASSWORD", "secret"),
    )


@pytest.fixture
def mail_imap() -> ImapConfig:
    """IMAP-конфиг для приёма ответов бота (ящик tester)."""
    return ImapConfig(
        host=os.environ.get("TEST_IMAP_HOST", "localhost"),
        port=int(os.environ.get("TEST_IMAP_PORT", "3143")),
        user=os.environ.get("TEST_IMAP_USER", "tester"),
        password=os.environ.get("TEST_IMAP_PASSWORD", "secret"),
    )


@pytest.fixture
def mailhog_api() -> str:
    """Базовый URL MailHog API (опционально, если используется MailHog)."""
    return os.environ.get("TEST_MAILHOG_API", "http://localhost:8025")


@pytest.fixture
def mail_server_available(mail_smtp: SmtpConfig) -> bool:
    """Доступен ли локальный почтовый стенд."""
    return _port_open(mail_smtp.host, mail_smtp.port)


@pytest.fixture
def email_test_env_json(tmp_path) -> str:
    """env.json с включённым email для интеграционных тестов Chat/Transport."""
    data = {
        "profiles": {
            "default": {
                "telegram_token": "dummy",
                "ai_api_key": "sk-test",
                "ai_base_url": "https://api.openai.com/v1",
                "ai_model": "gpt-4o-mini",
                "history_max_turns": 10,
                "telegram": {"message_max_length": 4000},
                "odata": {"default_top": 20, "max_top": 50},
                "formatter": {"enabled": True, "formatter_model": "gpt-4o-mini"},
                "email": {
                    "enabled": True,
                    "imap_host": "localhost",
                    "imap_port": 3143,
                    "imap_user": "bot",
                    "imap_password": "secret",
                    "imap_use_ssl": False,
                    "smtp_host": "localhost",
                    "smtp_port": 3025,
                    "smtp_user": "bot",
                    "smtp_password": "secret",
                    "smtp_use_ssl": False,
                    "smtp_use_tls": False,
                    "from_address": "bot@local.test",
                    "poll_interval": 2,
                    "allowed_senders": ["tester@local.test"],
                    "inline_max_chars": 100,
                    "inline_preview_chars": 40,
                    "max_fetch_records": 500,
                },
                "agents": {},
            }
        }
    }
    path = tmp_path / "env.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture
def require_live_ai():
    """Пропустить тест, если нет ключа AI для slow E2E."""
    key = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("AI_API_KEY / OPENAI_API_KEY not set")
    return key


@pytest.fixture
def require_odata_url():
    """Пропустить тест, если не задан URL OData тестовой ИБ."""
    url = os.environ.get("ODATA_URL", "")
    if not url:
        pytest.skip("ODATA_URL not set")
    return url


@pytest.fixture
def require_mail_server(mail_server_available: bool):
    """Пропустить E2E, если почтовый стенд не запущен."""
    if not mail_server_available:
        pytest.skip("Mail test server not available (run docker compose -f docker-compose.test.yml up -d)")
    return True


@pytest.fixture
def env_test_file() -> str | None:
    """Путь к env.test.json, если файл существует."""
    root = Path(__file__).resolve().parent.parent
    path = root / "env.test.json"
    return str(path) if path.is_file() else None
