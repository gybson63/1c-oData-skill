# Структура проекта

```
1c-oData-skill/
├── bot/                          # Telegram + email бот
│   ├── __main__.py               # python -m bot
│   ├── bot.py                    # Handlers, роутер агентов
│   ├── email_bot.py              # Email polling
│   ├── chat.py                   # ChatManager, Chat
│   ├── config.py                 # Pydantic Settings
│   ├── mcp_client.py             # MCP stdio/SSE
│   ├── mcp_config.py             # Merge shared + per-agent MCP
│   ├── logging_config.py         # Structured logging
│   ├── metrics.py                # Метрики, CostLogger, CostAnalyzer
│   ├── history.py                # История диалогов
│   ├── config_hint.md            # Терминология конфигурации (OData)
│   ├── master_prompt.md          # Промпт форматтера
│   ├── README.md
│   ├── agents/
│   │   ├── base.py
│   │   ├── analyst/              # AnalystAgent, MetadataBrief
│   │   ├── odata/                # ODataAgent, pipeline, analytics
│   │   └── formatter/            # FormatterAgent (Telegram HTML)
│   └── email/                    # IMAP/SMTP transport, parser
│
├── bot_lib/                      # Shared libraries
│   ├── odata_client.py           # Async OData HTTP + retry
│   ├── metadata_parser.py        # Парсинг $metadata
│   ├── conf_doc_client.py        # HTTP conf-doc fallback
│   └── exceptions.py
│
├── mcp_servers/
│   └── odata_server.py           # MCP для 1С OData
│
├── skills/                       # AI agent skills (Cursor/Cline)
│   ├── odata/
│   ├── 1cconfinfo/
│   ├── conf-doc/
│   ├── analyst/
│   ├── analyst-conf-doc/
│   ├── analyst-domain/
│   ├── analyst-mcp/
│   └── analyst/profiles/         # zup-korp.md, _template.md
│
├── processing/                   # EnableODataInterface EPF
│   ├── EnableODataInterface/
│   ├── EnableODataInterface.xml
│   └── EnableODataInterface.epf
│
├── docker/
│   └── searxng/settings.yml      # SearXNG config
│
├── docs/                         # Документация (см. docs/README.md)
├── tests/                        # pytest
├── scripts/                      # eval, probe, enable_searxng_mcp
├── examples/                     # check-availability.sh, enable-odata.bsl
│
├── docker-compose.searxng.yml
├── docker-compose.yml
├── env.example.json
├── mcp.example.json
├── mcp.analyst.example.json
└── requirements.txt
```

Подробная архитектура: [`architecture.md`](architecture.md).
