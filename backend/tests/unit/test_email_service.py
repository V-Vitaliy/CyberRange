from unittest.mock import MagicMock, Mock

import pytest

from app.agents.tools import email_service
from app.agents.tools.email_service import EmailService


def test_email_mock_cannot_connect_to_smtp(monkeypatch):
    from app.agents.tools import email_service

    monkeypatch.setattr(email_service.settings, "SMTP_HOST", "")
    smtp = Mock(side_effect=AssertionError("Real SMTP must not be contacted"))
    monkeypatch.setattr(email_service.smtplib, "SMTP", smtp)
    assert EmailService.send_real_email("test@example.invalid", "Test", "test") is True
    smtp.assert_not_called()


@pytest.mark.parametrize(
    "address,allowed",
    [
        ("student@lab.local", True),
        ("Student@LAB.local", True),
        ("attacker@example.com", False),
        ("student@lab.local, attacker@example.com", False),
        ("attacker@example.com, student@lab.local", False),
        ("attacker@example.com@lab.local", False),
        ("student@lab.local\nBcc: attacker@example.com", False),
        ("Student <student@lab.local>", False),
        ("lab.local", False),
    ],
)
def test_only_single_lab_domain_recipient_is_allowed(monkeypatch, address, allowed):
    monkeypatch.setattr(email_service.settings, "EMAIL_ALLOWED_DOMAINS", "lab.local")
    assert EmailService.recipient_allowed(address) is allowed


@pytest.mark.parametrize("starttls", [True, False])
def test_smtp_delivery_uses_explicit_recipient_and_optional_starttls(monkeypatch, starttls):
    monkeypatch.setattr(email_service.settings, "SMTP_HOST", "smtp.lab.invalid")
    monkeypatch.setattr(email_service.settings, "SMTP_STARTTLS", starttls)
    monkeypatch.setattr(email_service.settings, "SMTP_USER", "")
    server = MagicMock()
    monkeypatch.setattr(email_service.smtplib, "SMTP", Mock(return_value=server))
    assert EmailService.send_real_email("student@lab.local", "Test", "Hello") is True
    connection = server.__enter__.return_value
    assert connection.starttls.called is starttls
    assert connection.send_message.call_args.kwargs["to_addrs"] == ["student@lab.local"]
