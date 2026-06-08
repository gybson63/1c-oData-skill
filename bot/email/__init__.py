"""Email transport module."""

from bot.email.attachments import extract_text_from_attachment, format_attachments_for_context
from bot.email.parser import parse_email_message
from bot.email.store import EmailThreadStore
from bot.email.thread_context import ThreadContextConfig, build_user_text_from_thread

__all__ = [
    "EmailThreadStore",
    "ThreadContextConfig",
    "build_user_text_from_thread",
    "extract_text_from_attachment",
    "format_attachments_for_context",
    "parse_email_message",
]
