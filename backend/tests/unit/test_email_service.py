from unittest.mock import Mock

from app.services.email_service import EmailService


def test_email_mock_cannot_connect_to_smtp(monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(email_service.settings, "SMTP_HOST", "")
    smtp = Mock(side_effect=AssertionError("Real SMTP must not be contacted"))
    monkeypatch.setattr(email_service.smtplib, "SMTP", smtp)
    assert EmailService.send_real_email("test@example.invalid", "Test", "test") is True
    smtp.assert_not_called()
