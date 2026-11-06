import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

logger = logging.getLogger("uvicorn.error")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def smtp_configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))


def send_email(to_email: str, subject: str, html: str, text: str) -> None:
    """Send HTML plus plain text. If SMTP is not configured, log the HTML and return."""
    if not smtp_configured():
        logger.info("SMTP not configured. To %s | %s\n%s", to_email, subject, html)
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((_env("SMTP_FROM_NAME") or "Paiger", _env("SMTP_FROM")))
    message["To"] = to_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT") or "587")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                if _env("SMTP_PORT") != "25":
                    try:
                        smtp.starttls()
                        smtp.ehlo()
                    except smtplib.SMTPException:
                        logger.exception("SMTP STARTTLS failed")
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
    except Exception:
        logger.exception("Could not send email to %s", to_email)
