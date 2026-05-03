"""Общие фикстуры для тестов."""

import pytest


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