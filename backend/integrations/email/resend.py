"""Resend email integration via the Resend REST API.

Docs: https://resend.com/docs/api-reference/emails/send-email
"""
from __future__ import annotations

import logging
import os
import re
import socket
from typing import Any

import httpx

from backend.integrations.email.base_email import BaseEmail
from backend.integrations.email.signature import signature_html, signature_text

logger = logging.getLogger("gtm.integrations.resend")

_RESEND_SEND_URL = "https://api.resend.com/emails"
_TIMEOUT = 20.0
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mx_exists(domain: str) -> bool:
    try:
        import dns.resolver  # type: ignore[import]
        answers = dns.resolver.resolve(domain, "MX", lifetime=3)
        return len(answers) > 0
    except Exception:
        try:
            socket.getaddrinfo(domain, None)
            return True
        except OSError:
            return False


class ResendEmailClient(BaseEmail):
    provider_name = "resend"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        from_address: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("RESEND_API_KEY", "")
        self.from_address = from_address or os.getenv("RESEND_FROM_EMAIL", "Outreach <outreach@sgctech.ai>")

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        from_address: str | None = None,
    ) -> dict[str, Any]:
        sender = from_address or self.from_address
        if not self.api_key:
            return {"provider": self.provider_name, "to": to, "subject": subject, "status": "skipped", "reason": "no_api_key"}
        if not sender:
            return {"provider": self.provider_name, "to": to, "subject": subject, "status": "error", "reason": "no_from_address"}

        payload = {
            "from": sender,
            "to": [to],
            "subject": subject,
            "text": body + signature_text(),
            "html": f"<p>{body.replace(chr(10), '<br>')}</p>" + signature_html(),
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _RESEND_SEND_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
                return {"provider": self.provider_name, "to": to, "subject": subject, "from_address": sender, "message_id": result.get("id"), "status": "sent"}
        except httpx.HTTPStatusError as exc:
            return {"provider": self.provider_name, "to": to, "status": "error", "detail": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            return {"provider": self.provider_name, "to": to, "status": "error", "detail": str(exc)}

    async def check_deliverability(self, *, email: str) -> dict[str, Any]:
        if not _EMAIL_RE.match(email):
            return {"provider": self.provider_name, "email": email, "score": 0.0, "status": "invalid_format"}
        domain = email.split("@", 1)[1]
        has_mx = _mx_exists(domain)
        return {"provider": self.provider_name, "email": email, "score": 0.9 if has_mx else 0.5, "status": "valid" if has_mx else "no_mx"}


def verify_webhook_signature(*, payload: bytes, headers: dict[str, str], secret: str | None = None) -> bool:
    signing_secret = secret or os.getenv("RESEND_WEBHOOK_SECRET", "")
    if not signing_secret:
        return False
    try:
        from svix.webhooks import Webhook
    except ImportError:
        return False
    svix_id = headers.get("svix-id", "")
    svix_timestamp = headers.get("svix-timestamp", "")
    svix_signature = headers.get("svix-signature", "")
    if not (svix_id and svix_timestamp and svix_signature):
        return False
    wh = Webhook(signing_secret)
    try:
        wh.verify(payload, headers)
        return True
    except Exception:
        return False


def parse_webhook_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = payload.get("type", "")
    data = payload.get("data", {})
    return {
        "event_type": event_type,
        "provider_message_id": data.get("email_id") or data.get("id"),
        "to": data.get("to", []),
        "from": data.get("from"),
        "subject": data.get("subject"),
        "created_at": data.get("created_at"),
        "bounce_type": data.get("bounce", {}).get("type") if event_type == "bounced" else None,
        "click_url": data.get("click", {}).get("link") if event_type == "clicked" else None,
        "raw": payload,
    }
