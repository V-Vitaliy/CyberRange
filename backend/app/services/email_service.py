import logging
import smtplib
from email.message import EmailMessage
from app.core.config import settings

logger = logging.getLogger(__name__)

class EmailService:
    """
    Handles real email dispatching via SMTP.
    """
    @staticmethod
    def send_real_email(to_address: str, subject: str, body: str) -> bool:
        try:
            if not getattr(settings, "SMTP_HOST", None):
                logger.warning(f"[EMAIL MOCK] Would send to {to_address} | Subject: {subject}")
                logger.info(f"Body: {body}")
                return True

            msg = EmailMessage()
            msg.set_content(body)
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_USER
            msg["To"] = to_address

            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
            server.starttls()

            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)

            server.send_message(msg)
            server.quit()

            logger.info(f"[EMAIL SERVICE] Successfully sent email to {to_address}")
            return True

        except Exception as e:
            logger.error(f"[EMAIL SERVICE] Failed to send email to {to_address}: {e}")
            return False