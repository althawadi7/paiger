import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("uvicorn.error")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _safe(text: str) -> str:
    secret = _env("SMTP_PASSWORD") or _env("MAIL_PASSWORD")
    if secret and secret in text:
        text = text.replace(secret, "***")
    return text[:2000]


def smtp_profile() -> Dict[str, Any]:
    """SMTP_* wins when the process sets it. Otherwise the MAIL_* values in .env."""
    if _env("SMTP_HOST"):
        port = int(_env("SMTP_PORT") or "587")
        encryption = "ssl" if port == 465 else "none"
        return {
            "host": _env("SMTP_HOST"),
            "port": port,
            "user": _env("SMTP_USER"),
            "password": _env("SMTP_PASSWORD"),
            "sender": _env("SMTP_FROM"),
            "name": _env("SMTP_FROM_NAME") or "Paiger",
            "encryption": encryption,
        }
    port = int(_env("MAIL_PORT") or "587")
    encryption = _env("MAIL_ENCRYPTION").lower()
    if not encryption and port == 465:
        encryption = "ssl"
    return {
        "host": _env("MAIL_HOST"),
        "port": port,
        "user": _env("MAIL_USERNAME"),
        "password": _env("MAIL_PASSWORD"),
        "sender": _env("MAIL_FROM_ADDRESS"),
        "name": _env("MAIL_FROM_NAME") or "Paiger",
        "encryption": encryption,
    }


def smtp_configured() -> bool:
    profile = smtp_profile()
    return bool(profile["host"] and profile["sender"])


def deliver(
    to_email: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str = "",
    attachments: Optional[List[Tuple[str, bytes, str]]] = None,
) -> Dict[str, str]:
    """Send one message. Never raises. status is sent, failed, or skipped."""
    if not smtp_configured():
        return {"status": "skipped", "error": "SMTP is not configured", "response": ""}
    profile = smtp_profile()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((profile["name"] or "Paiger", profile["sender"]))
    message["To"] = to_email
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(text or "")
    message.add_alternative(html or "", subtype="html")
    for filename, content, mime in attachments or []:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        message.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)
    captured: List[str] = []
    try:
        smtp = smtplib.SMTP_SSL(profile["host"], profile["port"], timeout=20) if profile["encryption"] == "ssl" else smtplib.SMTP(profile["host"], profile["port"], timeout=20)
        with smtp:
            original = smtp.getreply

            def remember():
                code, reply = original()
                body = reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
                captured.append(f"{code} {body}".strip())
                return code, reply

            smtp.getreply = remember
            if profile["encryption"] != "ssl":
                smtp.ehlo()
                if profile["encryption"] != "none" and profile["port"] != 25:
                    smtp.starttls()
                    smtp.ehlo()
            if profile["user"]:
                smtp.login(profile["user"], profile["password"])
            refused = smtp.send_message(message)
        accepted = [line for line in captured if line.startswith("250")]
        response = accepted[-1] if accepted else (captured[-1] if captured else "")
        if refused:
            return {"status": "failed", "error": _safe(str(refused)), "response": response}
        return {"status": "sent", "error": "", "response": response}
    except Exception as exc:
        logger.exception("Could not send email to %s", to_email)
        return {"status": "failed", "error": _safe(str(exc)), "response": captured[-1] if captured else ""}


def send_email(to_email: str, subject: str, html: str, text: str) -> None:
    """Send HTML plus plain text. If SMTP is not configured, log the HTML and return."""
    result = deliver(to_email, subject, html, text)
    if result["status"] == "skipped":
        logger.info("SMTP not configured. To %s | %s\n%s", to_email, subject, html)
    elif result["status"] == "failed":
        logger.error("Could not send email to %s: %s", to_email, result["error"])
