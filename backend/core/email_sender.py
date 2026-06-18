from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Outreach <outreach@sgctech.ai>")


def _send_sync(to_email: str, subject: str, body: str) -> str:
    import resend  # type: ignore[import]
    resend.api_key = os.getenv("RESEND_API_KEY", "")
    params: resend.Emails.SendParams = {
        "from": _FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": f"<p>{body.replace(chr(10), '<br>')}</p>",
        "text": body,
    }
    result = resend.Emails.send(params)
    return result.get("id", "")


async def send_sequence_email(to_email: str, subject: str, body: str) -> str:
    """Send an approved email sequence via Resend. Returns the message ID."""
    try:
        message_id = await asyncio.to_thread(_send_sync, to_email, subject, body)
        logger.info("Resend sent to %s, id=%s", to_email, message_id)
        return message_id
    except Exception as exc:
        logger.error("Resend failed to %s: %s", to_email, exc)
        raise
