#!/usr/bin/env python3
"""OData-агент: обработка запросов к 1С через OData REST API.

Двухшаговая обработка:
  Шаг 1 — AI формирует OData-запрос (JSON) с помощью function calling
  Шаг 2 — AI форматирует результат для Telegram

Координация модулей:
  - :mod:`bot.agents.odata.metadata` — кэш и загрузка $metadata
  - :mod:`bot.agents.odata.odata_http` — выполнение OData-запросов
  - :mod:`bot.agents.odata.query_builder` — построение $expand, URL-лимиты
  - :mod:`bot.agents.odata.response_parser` — разрешение ссылок, подготовка данных
  - :mod:`bot.agents.odata.prompts` — промпты и инструменты для AI
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from bot.agents.base import BaseAgent
from bot.config import get_settings
from bot.metrics import metrics, save_provider_response, session_tokens, track_time
from bot.utils import RateLimiter, esc_html
from bot_lib.exceptions import AIError, AIRateLimitError, AIResponseError, ODataError

from .metadata import MetadataCache, fetch_metadata_from_server
from .odata_http import execute_odata_query
from .prompts import ODATA_REFERENCE, STEP1_SYSTEM, STEP2_SYSTEM, make_step1_tools
from .query_builder import build_expand, trim_expand_for_url_limit
from .response_parser import resolve_references

log = logging.getLogger(__name__)

# Значения по умолчанию (переопределяются из конфигурации)
_DEFAULT_HISTORY_MAX_TURNS = 10
_DEFAULT_DEFAULT_TOP = 20
_DEFAULT_MAX_TOP = 50
_DEFAULT_MAX_EXPAND_FIELDS = 15
_DEFAULT_MAX_URL_LENGTH = 1800
_DEFAULT_MAX_SAMPLE_RECORDS = 30
_DEFAULT_MAX_DATA_LENGTH = 8000
_DEFAULT_STEP1_TEMPERATURE = 0.1
_DEFAULT_STEP2_TEMPERATURE = 0.3

# ContextVar для передачи chat_id в глубину стека вызовов (async-safe)
_current_chat_id: ContextVar[int | None] = ContextVar("_current_chat_id", default=None)


class QueryError(Exception):
    """Ошибка разбора запроса."""
    pass


# Маппинг кодов ошибок OData 1С на человекопонятные сообщения
_ODATA_ERROR_CODES: dict[str, str] = {
    "0":  "Параметр не поддерживается (возможна опечатка в имени параметра).",
    "6":  "Метод не найден — проверьте имя виртуальной таблицы (слитно, без подчёркивания).",
    "8":  "Тип сущности не найден — проверьте имя объекта (префикс_Имя).",
    "9":  "Экземпляр сущности не найден — несуществующий GUID или ссылка.",
    "14": "Ошибка разбора $filter — проверьте синтаксис фильтра.",
}


def _parse_odata_error_message(error: ODataError) -> str:
    """Извлечь человекопонятное описание из OData-ошибки.

    Пытается распарсить JSON-тело ответа с кодом ошибки 1С.
    """
    msg = error.message or ""
    # Попытка найти JSON с odata.error в теле сообщения
    import json as _json
    try:
        # HTTP-ошибка содержит тело ответа после двоеточия
        body_start = msg.find("{")
        if body_start != -1:
            body = _json.loads(msg[body_start:])
            err = body.get("odata.error") or body.get("error") or body
            code = str(err.get("code", ""))
            message_value = err.get("message", "")
            if isinstance(message_value, dict):
                message_value = message_value.get("value", "")
            hint = _ODATA_ERROR_CODES.get(code)
            if hint:
                return f"{hint} ({message_value})" if message_value else hint
            if message_value:
                return str(message_value)
    except Exception:
        pass
    return msg


class ODataAgent(BaseAgent):
    """Агент для работы с 1С OData."""

    name = "odata"

    def __init__(self) -> None:
        super().__init__()
        self._ai_client: AsyncOpenAI | None = None
        self._metadata = MetadataCache()
        self._mcp_manager = None
        self._cfg: dict[str, Any] = {}
        self._model: str = ""
        self._rate_limiter: RateLimiter | None = None
        self._tools: list[dict] = []
        self._tools_map: dict[str, Any] = {}
        self._tools_supported: bool = True
        # Настраиваемые параметры (из конфигурации)
        self._history_max_turns: int = _DEFAULT_HISTORY_MAX_TURNS
        self._default_top: int = _DEFAULT_DEFAULT_TOP
        self._max_top: int = _DEFAULT_MAX_TOP
        self._max_expand_fields: int = _DEFAULT_MAX_EXPAND_FIELDS
        self._max_url_length: int = _DEFAULT_MAX_URL_LENGTH
        self._max_sample_records: int = _DEFAULT_MAX_SAMPLE_RECORDS
        self._max_data_length: int = _DEFAULT_MAX_DATA_LENGTH
        self._step1_temperature: float = _DEFAULT_STEP1_TEMPERATURE
        self._step2_temperature: float = _DEFAULT_STEP2_TEMPERATURE
        self._request_timeout: int = 60
        self._metadata_cache_seconds: int = 86400
        # Контекст пагинации: chat_id → последний запрос
        self._pagination_states: dict[int, dict[str, Any]] = {}

    # -- helpers --

    def _auth_header(self) -> str:
        import base64
        u, p = self._cfg["odata_user"], self._cfg["odata_password"]
        token = base64.b64encode(f"{u}:{p}".encode()).decode()
        return f"Basic {token}"

    # -- lifecycle --

    async def initialize(
        self,
        agent_config: dict[str, Any],
        global_config: dict[str, Any],
        cache_dir: str = ".cache",
        env_file: str = "env.json",
    ) -> None:
        # Мерж agent_config + global_config для обратной совместимости
        self._cfg = {**global_config, **agent_config}

        # Типизированные настройки через Pydantic Settings
        settings = get_settings()
        ai = settings.ai
        odata = settings.odata_query

        self._model = ai.model
        self._metadata = MetadataCache(cache_dir, cache_seconds=odata.metadata_cache_seconds)
        self._metadata_cache_seconds = odata.metadata_cache_seconds

        # AI client
        self._ai_client = AsyncOpenAI(
            api_key=ai.api_key,
            base_url=ai.base_url,
            max_retries=0,
        )

        # Rate limiter
        self._rate_limiter = RateLimiter(rpm=ai.rpm)

        # Настраиваемые параметры OData — из типизированной конфигурации
        self._history_max_turns = settings.history_max_turns
        self._default_top = odata.default_top
        self._max_top = odata.max_top
        self._max_expand_fields = odata.max_expand_fields
        self._max_url_length = odata.max_url_length
        self._request_timeout = odata.request_timeout
        self._max_sample_records = odata.max_sample_records
        self._max_data_length = odata.max_data_length
        self._step1_temperature = ai.temperature
        self._step2_temperature = ai.temperature_step2

        # MCP
        mcp_config = agent_config.get("mcp_servers", {})
        if mcp_config:
            from bot.mcp_client import MCPClientManager
            self._mcp_manager = MCPClientManager()
            await self._mcp_manager.connect_all(mcp_config)
            if self._mcp_manager.is_connected():
                status = self._mcp_manager.get_status()
                for srv, info in status.items():
                    log.info("ODataAgent MCP [%s]: transport=%s, tools=%s", srv, info["transport"], info["tools"])

        # Metadata
        loaded = self._metadata.load_from_disk()
        if not loaded:
            await self._load_metadata(force=True)

        # AI tools
        ref_keys = list(ODATA_REFERENCE.keys())
        self._tools = make_step1_tools(ref_keys)
        self._tools_map = {t["function"]["name"]: t["function"] for t in self._tools}

        self._initialized = True
        log.info("ODataAgent инициализирован (сущностей: %d)", len(self._metadata.entities))

    async def shutdown(self) -> None:
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
        self._initialized = False
        log.info("ODataAgent остановлен")

    async def refresh(self) -> None:
        await self._load_metadata(force=True)
        log.info("ODataAgent: метаданные обновлены (%d сущностей)", len(self._metadata.entities))

    # -- metadata loading --

    async def _load_metadata(self, force: bool = False) -> None:
        if not force and self._metadata.is_loaded:
            return
        xml = await fetch_metadata_from_server(self._cfg["odata_url"], self._auth_header(), timeout=self._request_timeout)
        if xml:
            self._metadata.parse_and_store(xml)
        else:
            log.warning("Не удалось загрузить $metadata — будет использован кэш при наличии")

    # -- processing --

    async def process_message(
        self,
        user_text: str,
        history: list[dict[str, str]],
        *,
        chat_id: int | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """Основной метод обработки сообщения.

        Args:
            user_text: текст сообщения от пользователя
            history: история диалога
            chat_id: ID чата для трекинга токенов по сессии

        Returns:
            (answer_html, updated_history)
        """
        token = _current_chat_id.set(chat_id)
        try:
            return await self._process_internal(user_text, history)
        except ODataError as e:
            log.error("OData error: %s (status=%s)", e, e.status_code)
            if e.status_code == 401:
                answer = "🔒 <b>Ошибка авторизации в 1С.</b> Проверьте логин и пароль."
            elif e.status_code == 404:
                answer = "🔍 <b>Объект не найден в OData.</b> Возможно, он не опубликован в базе 1С."
            elif e.status_code >= 500:
                answer = "🛑 <b>Ошибка сервера 1С.</b> Попробуйте позже."
            else:
                parsed = _parse_odata_error_message(e)
                answer = f"❌ <b>Ошибка OData:</b> {esc_html(parsed)}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except AIRateLimitError as e:
            log.warning("AI rate limit: %s", e)
            answer = "⏳ <b>Превышен лимит запросов к AI.</b> Подождите минуту и повторите."
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except AIError as e:
            log.error("AI error: %s", e)
            answer = f"🤖 <b>Ошибка AI-сервиса:</b> {esc_html(str(e))}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except QueryError as e:
            answer = f"⚠️ {esc_html(str(e))}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except Exception:
            log.exception("Unexpected error in ODataAgent")
            answer = "💥 Произошла непредвиденная ошибка. Попробуйте позже."
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        finally:
            _current_chat_id.reset(token)

    # -- AI interaction helpers --

    def _is_tool_use_error(self, exc: BadRequestError) -> bool:
        """Проверить, связана ли ошибка с отсутствием поддержки tool use."""
        msg = str(exc).lower()
        return "tool use" in msg or "tool_choice" in msg or "functions" in msg

    def _build_step1_prompt(self) -> str:
        """Построить системный промпт для Шага 1 (с учетом поддержки инструментов)."""
        base = STEP1_SYSTEM.format(metadata=self._metadata.format_entity_list())
        if not self._tools_supported:
            ref_lines = [
                "\n\n--- СПРАВОЧНИК ODATA (инструменты недоступны через function calling, используй текстовый вызов) ---",
                "⚠️ Инструменты НЕ доступны через function calling. Вызывай их текстом:",
                "  search_entities(query='Организации')",
                "  get_entity_fields(entity_name='Catalog_Организации')",
                "  odata_reference(topic='filter')",
                "Система распознает текстовый вызов, выполнит его и автоматически повторит запрос с результатом.",
                "После получения результата — ТОЛЬКО JSON с entity/filter/select, без рассуждений!",
                "",
                "Если ты УВЕРЕН(А) в имени сущности (видишь точное совпадение в списке доступных объектов),",
                "используй его напрямую без вызова инструмента.",
            ]
            for topic, text in ODATA_REFERENCE.items():
                ref_lines.append(f"\n[{topic}]\n{text}")
            base += "\n".join(ref_lines)
        return base

    @staticmethod
    def _wrap_ai_error(exc: Exception) -> AIError:
        """Обернуть ошибку AI-провайдера в типизированное исключение."""
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg or "limit" in msg:
            return AIRateLimitError(f"Превышен лимит запросов: {exc}")
        return AIError(f"Ошибка AI-сервиса: {exc}")

    def _track_ai_response(self, response, step: str) -> None:
        """Извлечь usage из ответа AI и записать в метрики + session tokens."""
        usage = getattr(response, "usage", None)
        if usage:
            settings = get_settings()
            pricing = settings.ai_pricing
            input_price, output_price = pricing.get_prices(self._model)
            # Извлечь cost_rub из ответа провайдера (если доступен)
            cost_rub = getattr(usage, "cost_rub", None)
            in_tok = usage.prompt_tokens or 0
            out_tok = usage.completion_tokens or 0
            metrics.track_ai_usage(
                model=self._model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                input_price_per_1m=input_price,
                output_price_per_1m=output_price,
                cost_rub=cost_rub,
            )
            # Записать токены и стоимость в per-session трекер (если chat_id задан)
            chat_id = _current_chat_id.get()
            if chat_id is not None:
                cost_usd = (in_tok * input_price + out_tok * output_price) / 1_000_000
                session_tokens.record(
                    chat_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost_usd,
                    cost_rub=cost_rub or 0.0,
                )
            log.debug(
                "AI usage [%s]: model=%s in=%d out=%d cost_rub=%s chat_id=%s",
                step, self._model, in_tok, out_tok, cost_rub, chat_id,
            )

    async def _step1_call_ai(
        self,
        messages: list[dict],
        use_tools: bool,
    ):
        """Вызов AI для Шага 1 — с инструментами или без."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,  # type: ignore[arg-type]
            "temperature": self._step1_temperature,
        }
        if use_tools and self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"

        metrics.increment("ai_requests_step1")
        async with track_time("ai_step1"):
            try:
                resp = await self._ai_client.chat.completions.create(**kwargs)  # type: ignore[union-attr]
            except BadRequestError:
                raise
            except Exception as exc:
                raise self._wrap_ai_error(exc) from exc

        self._track_ai_response(resp, "step1")

        # Сохранить ответ провайдера
        save_provider_response(
            step="step1",
            model=self._model,
            request_messages=messages,
            response_data=resp.model_dump() if hasattr(resp, "model_dump") else str(resp),
        )
        return resp

    # -- tool call handling --

    def _handle_tool_call(self, name: str, args: dict) -> str:
        """Обработка вызова инструмента (function calling)."""
        if name == "odata_reference":
            topic = args.get("topic", "")
            return ODATA_REFERENCE.get(topic, f"Тема '{topic}' не найдена.")
        elif name == "get_entity_fields":
            entity_name = args.get("entity_name", "")
            fields = self._metadata.get_entity_fields(entity_name)
            if fields:
                return json.dumps({"entity": entity_name, "fields": fields}, ensure_ascii=False)
            return f"Сущность '{entity_name}' не найдена в метаданных."
        elif name == "search_entities":
            query = args.get("query", "")
            results = self._metadata.search_entities(query)
            if results:
                return json.dumps({"query": query, "results": results, "count": len(results)}, ensure_ascii=False)
            return f"По запросу '{query}' ничего не найдено. Попробуйте другое ключевое слово."
        return f"Неизвестный инструмент: {name}"

    # -- JSON extraction --

    # Известные имена инструментов (function calling)
    _TOOL_NAMES = frozenset({"odata_reference", "get_entity_fields", "search_entities"})

    @classmethod
    def _is_inline_tool_call(cls, parsed: dict) -> bool:
        """Проверить, выглядит ли JSON как встроенный вызов инструмента.

        Некоторые модели вместо function calling возвращают в content:
          {"name": "search_entities", "arguments": {"query": "..."}}
          {"function": "search_entities", "arguments": {"query": "..."}}
        """
        if not isinstance(parsed, dict) or "entity" in parsed:
            return False
        tool_name = parsed.get("name") or parsed.get("function")
        return tool_name in cls._TOOL_NAMES and isinstance(parsed.get("arguments"), dict)

    async def _resolve_inline_tool_call(
        self,
        messages: list[dict],
        parsed: dict,
        user_text: str,
    ) -> dict:
        """Обработать встроенный вызов инструмента и повторить запрос к AI.

        Args:
            messages: текущая история сообщений.
            parsed: JSON вида {"name/function": "...", "arguments": {...}}.
            user_text: оригинальный текст пользователя.

        Returns:
            Распарсенный OData-запрос (dict с entity/filter/...).
        """
        tool_name = parsed.get("name") or parsed.get("function") or ""
        tool_args = parsed["arguments"]
        log.warning("Обнаружен встроенный tool call в content: %s(%s)", tool_name, tool_args)

        # Выполнить инструмент
        result = self._handle_tool_call(tool_name, tool_args)
        log.info("Inline tool result: %s", result[:300] if result else "")

        # Добавить результат инструмента в контекст и повторить запрос
        tool_msg = (
            f"[Результат инструмента {tool_name}]: {result}\n\n"
            f"Теперь построй OData-запрос JSON для: {user_text}"
        )
        messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
        messages.append({"role": "user", "content": tool_msg})

        if self._rate_limiter:
            await self._rate_limiter.wait()

        resp = await self._step1_call_ai(messages, use_tools=self._tools_supported)
        content = resp.choices[0].message.content or ""

        # Обработать возможные tool_calls от повторного запроса
        msg = resp.choices[0].message
        if msg.tool_calls:
            msg = await self._resolve_tool_calls(messages, msg)
            content = msg.content or ""

        query = self._extract_json(content)
        if not query:
            raise QueryError(
                f"Не удалось разобрать запрос после вызова инструмента.\n\n"
                f"<pre>{esc_html(content[:500])}</pre>"
            )
        return query

    # -- text tool call parsing (for models without function calling) --

    # Regex для распознавания текстовых вызовов инструментов:
    #   search_entities(query='Организации')
    #   get_entity_fields(entity_name='Catalog_Организации')
    #   odata_reference(topic='filter')
    _TEXT_TOOL_RE = re.compile(
        r'\b(search_entities|get_entity_fields|odata_reference)\s*\(\s*'
        r'(\w+)\s*=\s*[\'"]([^\'"]*)[\'"]'
        r'\s*\)'
    )

    @classmethod
    def _parse_text_tool_call(cls, text: str) -> tuple[str, dict | None]:
        """Распознать текстовый вызов инструмента в ответе AI.

        Поддерживаемые форматы::
            search_entities(query='Организации')
            get_entity_fields(entity_name='Catalog_Организации')
            odata_reference(topic='filter')

        Returns:
            Кортеж (tool_name, args_dict) или None.
        """
        # Ищем все вызовы, берём первый
        for match in cls._TEXT_TOOL_RE.finditer(text):
            tool_name = match.group(1)
            param_name = match.group(2)
            param_value = match.group(3)
            if tool_name in cls._TOOL_NAMES:
                log.info("Распознан текстовый вызов инструмента: %s(%s='%s')", tool_name, param_name, param_value)
                return tool_name, {param_name: param_value}
        return None

    @classmethod
    def _has_text_tool_call(cls, text: str) -> bool:
        """Проверить, содержит ли текст вызов инструмента в текстовом формате."""
        return bool(cls._TEXT_TOOL_RE.search(text))

    async def _resolve_text_tool_call(
        self,
        messages: list[dict],
        tool_name: str,
        tool_args: dict,
        user_text: str,
    ) -> dict:
        """Обработать текстовый вызов инструмента и повторить запрос к AI.

        Args:
            messages: текущая история сообщений.
            tool_name: имя инструмента (search_entities, get_entity_fields, odata_reference).
            tool_args: аргументы инструмента.
            user_text: оригинальный текст пользователя.

        Returns:
            Распарсенный OData-запрос (dict с entity/filter/...).
        """
        log.warning("Обработка текстового tool call: %s(%s)", tool_name, tool_args)

        # Выполнить инструмент
        result = self._handle_tool_call(tool_name, tool_args)
        log.info("Text tool result: %s", result[:500] if result else "")

        # Добавить результат инструмента в контекст и повторить запрос
        tool_msg = (
            f"[Результат инструмента {tool_name}({tool_args})]: {result}\n\n"
            f"Теперь СРАЗУ построй OData-запрос JSON для: {user_text}\n"
            f"⚠️ ТОЛЬКО JSON, без рассуждений и без вызовов инструментов!"
        )
        messages.append({"role": "assistant", "content": f"{tool_name}({tool_args})"})
        messages.append({"role": "user", "content": tool_msg})

        if self._rate_limiter:
            await self._rate_limiter.wait()

        resp = await self._step1_call_ai(messages, use_tools=False)
        content = resp.choices[0].message.content or ""

        # Обработать возможные tool_calls от повторного запроса (маловероятно, но возможно)
        msg = resp.choices[0].message
        if msg.tool_calls:
            msg = await self._resolve_tool_calls(messages, msg)
            content = msg.content or ""

        query = self._extract_json(content)
        if not query:
            raise QueryError(
                f"Не удалось разобрать запрос после вызова инструмента.\n\n"
                f"<pre>{esc_html(content[:500])}</pre>"
            )
        return query

    def _guess_entity_from_text(self, content: str, user_text: str) -> str | None:
        """Попытаться извлечь ключевое слово для поиска сущности из текста.

        Используется как последний fallback, когда модель написала рассуждения
        вместо JSON и не вызвала инструмент даже текстом.
        """
        # Убираем распространённые стоп-слова и берём существительное
        stop_words = {
            "покажи", "показать", "список", "все", "всех", "дай", "дайте",
            "найди", "найти", "выведи", "получить", "сколько", "какие",
            "какой", "какая", "где", "кто", "что", "это", "мне", "нам",
            "можно", "нужно", "хочу", "посмотри", "посмотреть", "и", "в",
            "на", "из", "за", "по", "с", "от", "до", "для", "не", "а",
            "но", "к", "о", "у", "те", "эти", "тот", "тотже", "этот",
        }
        words = re.findall(r'[а-яА-ЯёЁa-zA-Z0-9]{3,}', user_text)
        keywords = [w for w in words if w.lower() not in stop_words]
        if keywords:
            # Берём первое содержательное слово — обычно это название объекта
            return keywords[0]
        return None

    async def _retry_with_search_results(
        self,
        messages: list[dict],
        search_query: str,
        search_results: list,
        user_text: str,
    ) -> dict | None:
        """Повторить запрос к AI с результатами поиска сущности.

        Args:
            messages: текущая история сообщений.
            search_query: запрос поиска.
            search_results: результаты search_entities.
            user_text: оригинальный текст пользователя.

        Returns:
            Распарсенный OData-запрос или None.
        """
        results_str = json.dumps({"query": search_query, "results": search_results, "count": len(search_results)}, ensure_ascii=False)
        retry_msg = (
            f"[Автоматический поиск сущностей по запросу '{search_query}']: {results_str}\n\n"
            f"Используй найденную сущность и СРАЗУ построй OData-запрос JSON для: {user_text}\n"
            f"⚠️ ТОЛЬКО JSON, без рассуждений! Выбери наиболее подходящую сущность из результатов."
        )
        messages.append({"role": "assistant", "content": f"search_entities(query='{search_query}')"})
        messages.append({"role": "user", "content": retry_msg})

        if self._rate_limiter:
            await self._rate_limiter.wait()

        try:
            resp = await self._step1_call_ai(messages, use_tools=False)
            content = resp.choices[0].message.content or ""
            query = self._extract_json(content)
            return query
        except Exception as e:
            log.warning("Retry with search results failed: %s", e)
            return None

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Извлечь JSON из текста ответа AI."""
        text = text.strip()
        # Убрать повторяющиеся префиксы вида "tool_calls:" перед JSON
        # Некоторые модели пишут: tool_calls:{"function":"...","arguments":{...}}
        text = re.sub(r'^(?:tool_calls:\s*)+', '', text)
        # Убрать markdown-обёртки
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Найти JSON-объект (с поддержкой вложенных фигурных скобок)
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # -- core processing --

    async def _process_internal(
        self,
        user_text: str,
        history: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """Внутренняя реализация двухшаговой обработки."""
        auth = self._auth_header()

        # ---- Шаг 1: AI формирует OData-запрос ----
        system_prompt = self._build_step1_prompt()

        messages = [{"role": "system", "content": system_prompt}] + list(history) + [{"role": "user", "content": user_text}]

        # Rate limit
        if self._rate_limiter:
            await self._rate_limiter.wait()

        use_tools = self._tools_supported
        try:
            resp1 = await self._step1_call_ai(messages, use_tools=use_tools)
        except BadRequestError as e:
            if use_tools and self._is_tool_use_error(e):
                log.warning("Модель %s не поддерживает tool use, повтор без инструментов", self._model)
                self._tools_supported = False
                system_prompt = self._build_step1_prompt()
                messages[0] = {"role": "system", "content": system_prompt}
                if self._rate_limiter:
                    await self._rate_limiter.wait()
                resp1 = await self._step1_call_ai(messages, use_tools=False)
            else:
                raise

        msg1 = resp1.choices[0].message
        log.info("STEP1 initial response: content=%r, tool_calls=%s",
                 (msg1.content or "")[:200] if msg1.content else None,
                 [tc.function.name for tc in msg1.tool_calls] if msg1.tool_calls else None)

        # Обработка function calls (до 2 раундов)
        msg1 = await self._resolve_tool_calls(messages, msg1)

        # Извлечь JSON из ответа
        content = msg1.content or ""
        log.info("STEP1 final content (len=%d): %s", len(content), content[:1000])
        query = self._extract_json(content)

        if not query:
            # --- Fallback 1: текстовый вызов инструмента ---
            text_tool = self._parse_text_tool_call(content)
            if text_tool:
                tool_name, tool_args = text_tool
                query = await self._resolve_text_tool_call(
                    messages, tool_name, tool_args, user_text,
                )
                log.info("STEP1 after text tool resolution: %s",
                         json.dumps(query, ensure_ascii=False)[:500])

        if not query:
            # --- Fallback 2: авто-поиск сущности по ключевому слову ---
            guessed = self._guess_entity_from_text(content, user_text)
            if guessed:
                log.info("Auto-searching entity by keyword: '%s'", guessed)
                results = self._metadata.search_entities(guessed)
                if results:
                    query = await self._retry_with_search_results(
                        messages, guessed, results, user_text,
                    )
                    if query:
                        log.info("STEP1 after auto-search fallback: %s",
                                 json.dumps(query, ensure_ascii=False)[:500])

        if not query:
            log.warning("Не удалось извлечь JSON из ответа AI. Полный ответ:\n%s", content)
            raise QueryError(
                f"Не удалось разобрать запрос. Попробуйте переформулировать.\n\n"
                f"<pre>{esc_html(content[:500])}</pre>"
            )

        log.info("STEP1 parsed query: %s", json.dumps(query, ensure_ascii=False)[:500])

        # Fallback: модель вернула tool call как JSON вместо function calling
        if self._is_inline_tool_call(query):
            query = await self._resolve_inline_tool_call(messages, query, user_text)
            log.info("STEP1 after inline tool resolution: %s", json.dumps(query, ensure_ascii=False)[:500])

        entity = query.get("entity", "")
        if not entity:
            raise QueryError("Не указана сущность (entity) в запросе.")

        do_count = query.get("count", False)

        if do_count:
            return await self._handle_count_query(user_text, history, query, entity, auth)

        # ---- Обычный запрос ----
        return await self._handle_data_query(user_text, history, query, entity, auth)

    async def _resolve_tool_calls(self, messages: list[dict], msg1) -> Any:
        """Обработать до 2 раундов function calls от AI.

        Args:
            messages: список сообщений (mutated — добавляются tool results).
            msg1: сообщение AI от Шага 1.

        Returns:
            Финальное сообщение AI после обработки всех tool calls.
        """
        for round_num in range(1, 3):
            if not msg1.tool_calls:
                break

            tool_results = []
            for tc in msg1.tool_calls:
                fn = tc.function
                log.info("Tool call (round %d): %s(%s)", round_num, fn.name, fn.arguments[:300] if fn.arguments else "")
                result = self._handle_tool_call(fn.name, json.loads(fn.arguments))
                log.info("Tool result (round %d): %s", round_num, result[:300] if result else "")
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.append(msg1.model_dump())  # type: ignore[arg-type]
            messages.extend(tool_results)

            if self._rate_limiter:
                await self._rate_limiter.wait()

            resp = await self._step1_call_ai(messages, use_tools=True)
            msg1 = resp.choices[0].message
            log.info("STEP1 after tools (round %d): content=%r",
                     round_num, (msg1.content or "")[:500] if msg1.content else None)

        return msg1

    async def _handle_count_query(
        self,
        user_text: str,
        history: list[dict[str, str]],
        query: dict,
        entity: str,
        auth: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Обработать запрос на подсчёт записей."""
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=query.get("filter"),
            count=True,
            request_timeout=self._request_timeout,
        )
        answer = f"<b>📊 Количество</b> <i>{esc_html(entity)}</i>: <code>{total}</code>"
        if query.get("explanation"):
            answer += f"\n<i>{esc_html(query['explanation'])}</i>"

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": json.dumps({"entity": entity, "filter": query.get("filter"), "count": True, "explanation": query.get("explanation", "")}, ensure_ascii=False)})
        return answer, history[-(self._history_max_turns * 2):]

    async def _handle_data_query(
        self,
        user_text: str,
        history: list[dict[str, str]],
        query: dict,
        entity: str,
        auth: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Обработать запрос данных сущности."""
        top = min(int(query.get("top") or self._default_top), self._max_top)
        skip = query.get("skip")
        if skip is not None:
            skip = int(skip)

        # Валидация $select
        select = query.get("select")
        if isinstance(select, list):
            select = ",".join(str(s) for s in select)
        orderby = query.get("orderby")
        if isinstance(orderby, list):
            orderby = ",".join(str(s) for s in orderby)

        fields = self._metadata.get_entity_fields(entity)
        if fields:
            log.info("Fields for %s: %s", entity, fields)
            select = self._validate_select(fields, select)
            orderby = self._validate_orderby(fields, orderby)

        # Построить $expand для раскрытия ссылочных полей
        expand = build_expand(entity, select, fields, self._max_expand_fields)

        # Проверить длину URL и при необходимости сократить $expand
        filter_expr = query.get("filter")
        expand = trim_expand_for_url_limit(
            self._cfg["odata_url"], entity, filter_expr, select, orderby, top, expand,
            max_url_length=self._max_url_length,
        )

        # Выполнение OData-запроса
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=filter_expr,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            request_timeout=self._request_timeout,
        )

        # Fallback: если 0 записей и фильтр содержит Number + любую дату
        records, total = await self._fallback_date_filter(
            records, total, entity, filter_expr, select, orderby, top, skip, expand, auth,
        )

        # Fallback 2: substringof (OData v3)
        records, total = await self._fallback_substringof(
            records, total, entity, filter_expr, select, orderby, top, skip, expand, auth,
        )

        # ---- Шаг 2: AI форматирует ответ ----
        shown = len(records)
        answer = await self._ai_format_response(
            user_text, records, total, entity, shown=shown, skip=skip or 0,
        )

        # Сохранить контекст пагинации в историю (JSON в assistant-сообщении)
        pagination_ctx = {
            "entity": entity,
            "filter": filter_expr,      # валидированный фильтр (после fallback)
            "select": select,           # валидированный select (только существующие поля)
            "orderby": orderby,         # валидированный orderby
            "top": top,
            "skip": skip or 0,
            "total": total,
            "shown": shown,
            "expand": expand,
            "explanation": query.get("explanation", ""),
        }
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": json.dumps(pagination_ctx, ensure_ascii=False)})
        return answer, history[-(self._history_max_turns * 2):]

    # -- query validation helpers --

    @staticmethod
    def _validate_select(fields: list[str], select: str | None) -> str | None:
        """Скорректировать $select, оставив только существующие поля."""
        if not select:
            return select
        raw_select = select[len("$select="):] if select.startswith("$select=") else select
        valid = [f.strip() for f in raw_select.split(",") if f.strip() in fields]
        result = ",".join(valid) if valid else None
        if result != raw_select:
            log.info("$select скорректирован: %s → %s", raw_select, result)
        return result

    @staticmethod
    def _validate_orderby(fields: list[str], orderby: str | None) -> str | None:
        """Скорректировать $orderby, проверив что поле существует."""
        if not orderby:
            return orderby
        raw_orderby = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
        field_name = raw_orderby.split()[0]
        if field_name not in fields:
            log.info("$orderby '%s' не найден в полях, убираем", field_name)
            return None
        return orderby

    # -- fallback strategies --

    async def _fallback_date_filter(
        self,
        records: list[dict],
        total: int,
        entity: str,
        filter_expr: str | None,
        select: str | None,
        orderby: str | None,
        top: int,
        skip: int | None,
        expand: str | None,
        auth: str,
    ) -> tuple[list[dict], int]:
        """Fallback: убрать условия с datetime если 0 записей и фильтр содержит Number."""
        if total != 0 or not filter_expr or "Number" not in filter_expr:
            return records, total

        fallback_filter = re.sub(
            r"\s*and\s+\w+\s+(eq|ge|le|gt|lt)\s+datetime'[^']*'",
            "", filter_expr,
        )
        if fallback_filter == filter_expr:
            return records, total

        log.info("Fallback 1: retry without any date filter: %s", fallback_filter)
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=fallback_filter,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            request_timeout=self._request_timeout,
        )
        return records, total

    async def _fallback_substringof(
        self,
        records: list[dict],
        total: int,
        entity: str,
        filter_expr: str | None,
        select: str | None,
        orderby: str | None,
        top: int,
        skip: int | None,
        expand: str | None,
        auth: str,
    ) -> tuple[list[dict], int]:
        """Fallback 2: попробовать substringof если 0 записей с Number eq."""
        if total != 0 or not filter_expr or "Number eq '" not in filter_expr:
            return records, total

        number_match = re.search(r"Number eq '([^']*)'", filter_expr)
        if not number_match:
            return records, total

        num = number_match.group(1)
        digits = re.sub(r'^[^\d]+', '', num)
        if not digits or digits == num:
            return records, total

        contains_filter = f"DeletionMark eq false and substringof('{digits}', Number)"
        log.info("Fallback 2: retry with substringof('%s', Number): %s", digits, contains_filter)
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=contains_filter,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            request_timeout=self._request_timeout,
        )
        return records, total

    # -- AI formatting (Step 2) --

    async def _ai_format_response(
        self,
        user_text: str,
        records: list[dict],
        total: int,
        entity: str,
        shown: int = 0,
        skip: int = 0,
        prev_last_record: dict | None = None,
    ) -> str:
        """Шаг 2: AI форматирует записи в HTML-ответ для Telegram."""
        resolved = resolve_references(records)
        sample = resolved[:self._max_sample_records]
        data_str = json.dumps(sample, ensure_ascii=False, indent=2)
        if len(data_str) > self._max_data_length:
            data_str = data_str[:self._max_data_length] + "\n... (данные сокращены)"

        # Информация о пагинации для AI
        pagination_info = ""
        if shown > 0 and total > shown:
            pagination_info = f"\nПоказано записей: {shown} (пропущено: {skip})\nВсего записей в выборке: {total}\nЕсть ещё записи для пагинации."

        # Последний элемент предыдущей страницы (для визуальной непрерывности)
        prev_item_info = ""
        if prev_last_record is not None:
            prev_resolved = resolve_references([prev_last_record])
            prev_json = json.dumps(prev_resolved[0], ensure_ascii=False, indent=2)
            prev_item_info = (
                f"\n\n⚠️ ПОСЛЕДНИЙ ЭЛЕМЕНТ С ПРЕДЫДУЩЕЙ СТРАНИЦЫ (показать как контекст-напоминание, "
                f"без нумерации, курсивом, с разделителем '─── предыдущая страница ───'):\n{prev_json}"
            )

        messages = [
            {"role": "system", "content": STEP2_SYSTEM},
            {"role": "user", "content": (
                f"Вопрос: {user_text}\n\nСущность: {entity}\n"
                f"Всего записей: {total}{pagination_info}{prev_item_info}\n\n"
                f"Данные:\n{data_str}"
            )},
        ]

        if self._rate_limiter:
            await self._rate_limiter.wait()

        metrics.increment("ai_requests_step2")
        async with track_time("ai_step2"):
            try:
                resp = await self._ai_client.chat.completions.create(  # type: ignore[union-attr]
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self._step2_temperature,
                )
            except Exception as exc:
                raise self._wrap_ai_error(exc) from exc

        self._track_ai_response(resp, "step2")

        # Сохранить ответ провайдера
        save_provider_response(
            step="step2",
            model=self._model,
            request_messages=messages,
            response_data=resp.model_dump() if hasattr(resp, "model_dump") else str(resp),
        )

        content = resp.choices[0].message.content
        if not content:
            raise AIResponseError("AI вернул пустой ответ на шаге форматирования")
        return content

    # -- pagination --

    def save_pagination_state(self, chat_id: int, context: dict[str, Any]) -> None:
        """Сохранить контекст последнего запроса для пагинации."""
        self._pagination_states[chat_id] = context

    def get_pagination_state(self, chat_id: int) -> dict[str, Any | None]:
        """Получить контекст пагинации для чата."""
        return self._pagination_states.get(chat_id)

    def clear_pagination_state(self, chat_id: int) -> None:
        """Сбросить контекст пагинации (например, при /clear)."""
        self._pagination_states.pop(chat_id, None)

    async def execute_page(
        self,
        chat_id: int,
        skip: int,
    ) -> tuple[str, dict[str, Any | None]]:
        """Выполнить запрос с заданным skip (для inline-кнопок пагинации).

        Не проходит через AI Step 1 — использует сохранённый контекст запроса.
        Проходит через AI Step 2 для форматирования.

        Args:
            chat_id: идентификатор чата
            skip: значение $skip

        Returns:
            Кортеж (answer_html, pagination_context или None).
        """
        ctx = self._pagination_states.get(chat_id)
        if not ctx:
            return "⚠️ Контекст запроса потерян. Повторите запрос.", None

        entity = ctx["entity"]
        filter_expr = ctx.get("filter")
        select = ctx.get("select")
        orderby = ctx.get("orderby")
        top = ctx.get("top", self._default_top)
        expand = ctx.get("expand")

        auth = self._auth_header()

        # При пагинации (skip > 0) захватить последний элемент предыдущей страницы
        prev_last_record: dict | None = None
        effective_skip = skip
        effective_top = top

        if skip > 0:
            effective_skip = skip - 1
            effective_top = top + 1

        try:
            records, total = await execute_odata_query(
                odata_url=self._cfg["odata_url"],
                auth_header=auth,
                entity=entity,
                filter_expr=filter_expr,
                select=select,
                orderby=orderby,
                top=effective_top,
                skip=effective_skip,
                expand=expand,
                request_timeout=self._request_timeout,
            )
        except ODataError as e:
            log.error("Pagination OData error: %s", e)
            return f"❌ Ошибка запроса: {esc_html(str(e))}", None

        # Отделить «хвост» предыдущей страницы от текущей
        if skip > 0 and records:
            prev_last_record = records[0]
            records = records[1:]

        shown = len(records)
        user_text = f"Страница со смещением {skip}"
        answer = await self._ai_format_response(
            user_text, records, total, entity, shown=shown, skip=skip,
            prev_last_record=prev_last_record,
        )

        # Обновить контекст пагинации
        new_ctx = {
            **ctx,
            "skip": skip,
            "total": total,
            "shown": shown,
        }
        self._pagination_states[chat_id] = new_ctx

        return answer, new_ctx

    # -- status --

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "entities_count": len(self._metadata.entities),
            "mcp_connected": self._mcp_manager.is_connected() if self._mcp_manager else False,
            "model": self._model,
            "pagination_states": len(self._pagination_states),
        }
