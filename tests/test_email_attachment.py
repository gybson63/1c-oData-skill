"""Тесты прикрепления больших ответов к email."""

from bot.config import EmailSettings
from bot.email.attachment_builder import prepare_email_response


def test_inline_when_short():
    settings = EmailSettings(inline_max_chars=8000)
    body, attachments = prepare_email_response("Короткий ответ", settings)
    assert body == "Короткий ответ"
    assert attachments == []


def test_attachment_when_long():
    settings = EmailSettings(inline_max_chars=100, inline_preview_chars=50)
    long_answer = "A" * 500
    body, attachments = prepare_email_response(long_answer, settings, subject="Запрос")

    assert len(attachments) == 1
    assert attachments[0].filename.endswith(".html")
    assert attachments[0].size > 500
    assert "вложении" in body
    assert "Краткий просмотр" in body


def test_custom_attachment_filename():
    settings = EmailSettings(
        inline_max_chars=50,
        attachment_filename="report.html",
    )
    _, attachments = prepare_email_response("X" * 200, settings)
    assert attachments[0].filename == "report.html"


def test_token_footer_in_attachment():
    settings = EmailSettings(inline_max_chars=50)
    body, attachments = prepare_email_response(
        "Y" * 200,
        settings,
        token_footer="100 tokens",
    )
    assert attachments
    assert b"100 tokens" in attachments[0].data
    assert "100 tokens" not in body
