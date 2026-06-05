#!/usr/bin/env python3
"""Транспортно-независимые модели сообщений.

Единый формат для Telegram, Email и будущих каналов.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TransportChannel(StrEnum):
    """Канал доставки сообщения."""

    TELEGRAM = "telegram"
    EMAIL = "email"


@dataclass
class AgentProcessResult:
    """Результат обработки сообщения агентом."""

    text: str
    history: list[dict[str, str]]
    attachments: list[Attachment] = field(default_factory=list)
    chart_html: str = ""
    skip_formatter: bool = False


@dataclass
class Attachment:
    """Вложение к входящему сообщению."""

    filename: str
    content_type: str
    data: bytes
    size: int = 0

    def __post_init__(self) -> None:
        if not self.size:
            self.size = len(self.data)


@dataclass
class EmailMessageMeta:
    """Метаданные одного письма в цепочке."""

    message_id: str
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    subject: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    date: str = ""
    thread_id: str = ""


@dataclass
class InboundMessage:
    """Входящее сообщение от любого транспорта."""

    conversation_id: str
    channel: TransportChannel
    text: str
    sender: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Для email: полная цепочка писем, уже подготовленная для AI
    thread_context: str = ""

    @property
    def chat_id(self) -> int:
        """Числовой ID для совместимости с HistoryManager и session_tokens."""
        return conversation_id_to_chat_id(self.conversation_id)


@dataclass
class OutboundMessage:
    """Исходящее сообщение для любого транспорта."""

    text: str
    channel: TransportChannel
    format: str = "html"  # html | plain
    subject: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    reply_to_message_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def conversation_id_to_chat_id(conversation_id: str) -> int:
    """Стабильный числовой ID из строкового conversation_id.

    Telegram: ``tg:12345`` → положительный hash-подобный ID из числа.
    Email: ``email:thread-abc`` → отрицательный ID (не пересекается с Telegram).
    """
    if conversation_id.startswith("tg:"):
        try:
            return int(conversation_id[3:])
        except ValueError:
            pass

    digest = hashlib.sha256(conversation_id.encode()).hexdigest()
    numeric = int(digest[:15], 16) % (10**15)
    return -numeric if numeric else -1


def telegram_conversation_id(chat_id: int) -> str:
    """Conversation ID для Telegram-чата."""
    return f"tg:{chat_id}"


def email_conversation_id(thread_id: str) -> str:
    """Conversation ID для email-цепочки."""
    return f"email:{thread_id}"
