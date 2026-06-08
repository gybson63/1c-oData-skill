#!/usr/bin/env python3
"""Хранение цепочек писем и построение контекста для AI."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC
from pathlib import Path

from bot.messages import Attachment, EmailMessageMeta

log = logging.getLogger(__name__)


@dataclass
class ThreadMessage:
    """Одно письмо в цепочке."""

    meta: EmailMessageMeta
    body: str
    html_body: str = ""
    attachment_names: list[str] = field(default_factory=list)
    attachment_text: str = ""
    role: str = "user"  # user | assistant


class EmailThreadStore:
    """Персистентное хранилище цепочек писем."""

    def __init__(self, store_dir: str | Path) -> None:
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._processed_file = self._dir / "processed_uids.json"
        self._processed: set[str] = self._load_processed()

    def _load_processed(self) -> set[str]:
        if not self._processed_file.is_file():
            return set()
        try:
            data = json.loads(self._processed_file.read_text(encoding="utf-8"))
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def _save_processed(self) -> None:
        try:
            self._processed_file.write_text(
                json.dumps(sorted(self._processed), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("Не удалось сохранить processed UIDs: %s", e)

    def is_processed(self, uid: str) -> bool:
        return uid in self._processed

    def mark_processed(self, uid: str) -> None:
        self._processed.add(uid)
        self._save_processed()

    def _thread_path(self, thread_id: str) -> Path:
        safe = thread_id.replace("<", "").replace(">", "").replace("/", "_").replace("\\", "_")
        return self._dir / f"thread_{safe[:120]}.json"

    def add_message(
        self,
        meta: EmailMessageMeta,
        body: str,
        html_body: str = "",
        attachments: list[Attachment] | None = None,
        attachment_text: str = "",
    ) -> list[ThreadMessage]:
        """Добавить письмо в цепочку и вернуть полную цепочку."""
        thread = self.get_thread(meta.thread_id)

        # Дедупликация по message_id
        existing_ids = {m.meta.message_id for m in thread}
        if meta.message_id and meta.message_id in existing_ids:
            return thread

        att_names = [a.filename for a in (attachments or [])]
        thread.append(
            ThreadMessage(
                meta=meta,
                body=body,
                html_body=html_body,
                attachment_names=att_names,
                attachment_text=attachment_text,
            )
        )

        self._save_thread(meta.thread_id, thread)
        return thread

    def add_bot_reply(
        self,
        *,
        thread_id: str,
        message_id: str,
        body: str,
        subject: str = "",
        in_reply_to: str = "",
    ) -> list[ThreadMessage]:
        """Сохранить исходящий ответ бота в цепочке (для контекста follow-up)."""
        thread = self.get_thread(thread_id)
        existing_ids = {m.meta.message_id for m in thread}
        if message_id and message_id in existing_ids:
            return thread

        from datetime import datetime

        meta = EmailMessageMeta(
            message_id=message_id,
            in_reply_to=in_reply_to,
            subject=subject,
            sender="bot",
            date=datetime.now(UTC).isoformat(),
            thread_id=thread_id,
        )
        thread.append(
            ThreadMessage(
                meta=meta,
                body=body,
                role="assistant",
            )
        )
        self._save_thread(thread_id, thread)
        return thread

    def get_thread(self, thread_id: str) -> list[ThreadMessage]:
        path = self._thread_path(thread_id)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [_dict_to_thread_message(item) for item in data]
        except (json.JSONDecodeError, OSError, KeyError) as e:
            log.warning("Ошибка чтения цепочки %s: %s", thread_id, e)
            return []

    def _save_thread(self, thread_id: str, thread: list[ThreadMessage]) -> None:
        path = self._thread_path(thread_id)
        data = [_thread_message_to_dict(m) for m in thread]
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("Не удалось сохранить цепочку %s: %s", thread_id, e)


def _thread_message_to_dict(msg: ThreadMessage) -> dict:
    return {
        "meta": asdict(msg.meta),
        "body": msg.body,
        "html_body": msg.html_body,
        "attachment_names": msg.attachment_names,
        "attachment_text": msg.attachment_text,
        "role": msg.role,
    }


def _dict_to_thread_message(data: dict) -> ThreadMessage:
    meta = EmailMessageMeta(**data["meta"])
    return ThreadMessage(
        meta=meta,
        body=data.get("body", ""),
        html_body=data.get("html_body", ""),
        attachment_names=data.get("attachment_names", []),
        attachment_text=data.get("attachment_text", ""),
        role=data.get("role", "user"),
    )
