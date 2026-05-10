#!/usr/bin/env python3
"""Централизованная обработка ошибок OData-агента.

Извлекает паттерн try/except из :meth:`ODataAgent.process_message`
в отдельный модуль для повторного использования и тестирования.

Использование::

    handler = ErrorHandler(max_history_turns=10)
    answer, history = handler.handle(exc, user_text, history)
"""

from __future__ import annotations

import json
import logging

from bot.utils import esc_html
from bot_lib.exceptions import AIError, AIRateLimitError, ODataError

log = logging.getLogger(__name__)


# Маппинг кодов ошибок OData 1С на человекопонятные сообщения
_ODATA_ERROR_CODES: dict[str, str] = {
    "0":  "Параметр не поддерживается (возможна опечатка в имени параметра).",
    "6":  "Метод не найден — проверьте имя виртуальной таблицы (слитно, без подчёркивания).",
    "8":  "Тип сущности не найден — проверьте имя объекта (префикс_Имя).",
    "9":  "Экземпляр сущности не найден — несуществующий GUID или ссылка.",
    "14": "Ошибка разбора $filter — проверьте синтаксис фильтра.",
}


class QueryError(Exception):
    """Ошибка разбора запроса (не удалось извлечь OData JSON из ответа AI)."""


def parse_odata_error_message(error: ODataError) -> str:
    """Извлечь человекопонятное описание из OData-ошибки.

    Пытается распарсить JSON-тело ответа с кодом ошибки 1С.
    """
    msg = error.message or ""
    try:
        body_start = msg.find("{")
        if body_start != -1:
            body = json.loads(msg[body_start:])
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


class ErrorHandler:
    """Централизованный обработчик ошибок с формированием HTML-ответа.

    Преобразует типизированные исключения в пользовательские сообщения
    и обновляет историю диалога.
    """

    def __init__(self, max_history_turns: int = 10) -> None:
        self._max_turns = max_history_turns

    def handle(
        self,
        exc: Exception,
        user_text: str,
        history: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """Обработать исключение и вернуть (answer_html, updated_history).

        Args:
            exc: перехваченное исключение.
            user_text: текст сообщения пользователя.
            history: текущая история диалога.

        Returns:
            Кортеж (HTML-ответ, обрезанная история).
        """
        answer = self._format_answer(exc)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})
        return answer, history[-(self._max_turns * 2):]

    def _format_answer(self, exc: Exception) -> str:
        """Преобразовать исключение в HTML-ответ."""
        if isinstance(exc, ODataError):
            return self._format_odata_error(exc)
        if isinstance(exc, AIRateLimitError):
            log.warning("AI rate limit: %s", exc)
            return "⏳ <b>Превышен лимит запросов к AI.</b> Подождите минуту и повторите."
        if isinstance(exc, AIError):
            log.error("AI error: %s", exc)
            return f"🤖 <b>Ошибка AI-сервиса:</b> {esc_html(str(exc))}"
        if isinstance(exc, QueryError):
            return f"⚠️ {esc_html(str(exc))}"

        log.exception("Unexpected error in ODataAgent")
        return "💥 Произошла непредвиденная ошибка. Попробуйте позже."

    @staticmethod
    def _format_odata_error(exc: ODataError) -> str:
        """Форматировать OData-ошибку в HTML."""
        log.error("OData error: %s (status=%s)", exc, exc.status_code)
        if exc.status_code == 401:
            return "🔒 <b>Ошибка авторизации в 1С.</b> Проверьте логин и пароль."
        if exc.status_code == 404:
            return "🔍 <b>Объект не найден в OData.</b> Возможно, он не опубликован в базе 1С."
        if exc.status_code is not None and exc.status_code >= 500:
            return "🛑 <b>Ошибка сервера 1С.</b> Попробуйте позже."
        parsed = parse_odata_error_message(exc)
        return f"❌ <b>Ошибка OData:</b> {esc_html(parsed)}"
