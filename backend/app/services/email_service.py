import logging
import smtplib
from email.message import EmailMessage
from email.utils import getaddresses

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Delivers agent email through SMTP, restricted to the lab mail domains."""

    @staticmethod
    def recipient_allowed(to_address: str) -> bool:
        """Accept exactly one plain address whose domain is in EMAIL_ALLOWED_DOMAINS."""
        addresses = getaddresses([to_address])
        if len(addresses) != 1 or addresses[0][1] != to_address.strip():
            return False
        local, _, domain = addresses[0][1].rpartition("@")
        allowed = {d.strip().lower() for d in settings.EMAIL_ALLOWED_DOMAINS.split(",") if d.strip()}
        return bool(local) and "@" not in local and domain.lower() in allowed

    @staticmethod
    def send_real_email(to_address: str, subject: str, body: str) -> bool:
        try:
            if not settings.SMTP_HOST:
                logger.warning(f"[EMAIL MOCK] Would send to {to_address} | Subject: {subject}")
                logger.info(f"Body: {body}")
                return True

            msg = EmailMessage()
            msg.set_content(body)
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM
            msg["To"] = to_address

            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                if settings.SMTP_STARTTLS:
                    server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                # Explicit recipients: header parsing must not add addresses.
                server.send_message(msg, to_addrs=[to_address])

            logger.info(f"[EMAIL SERVICE] Successfully sent email to {to_address}")
            return True

        except Exception as e:
            logger.error(f"[EMAIL SERVICE] Failed to send email to {to_address}: {e}")
            return False
