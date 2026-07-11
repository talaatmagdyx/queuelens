"""SMTP delivery (Mailpit-friendly) with bounded retry."""

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)

RETRY_DELAYS = (0.5, 2.0, 8.0)  # 3 attempts with exponential backoff


def _send_sync(config: dict[str, Any], subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.get("from", "queuelens@localhost")
    message["To"] = config.get("to", "sre@localhost")
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(
        config.get("smtp_host", "localhost"), int(config.get("smtp_port", 1025)), timeout=10
    ) as smtp:
        smtp.send_message(message)


async def send_email(config: dict[str, Any], subject: str, body: str) -> dict[str, Any]:
    """Send an email, retrying on failure. Returns a delivery record."""
    attempts: list[str] = []
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            await asyncio.to_thread(_send_sync, config, subject, body)
            return {"ok": True, "attempts": attempt, "errors": attempts}
        except Exception as error:  # noqa: BLE001 - any SMTP failure is retryable
            attempts.append(str(error))
            logger.warning("email attempt %s failed: %s", attempt, error)
            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(delay)
    return {"ok": False, "attempts": len(RETRY_DELAYS), "errors": attempts}
