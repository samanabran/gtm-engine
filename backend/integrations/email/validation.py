"""Pre-send email validation to reduce bounces.

Two concrete, dependency-light layers (cheapest first):

1. Syntax + domain deliverability via the open-source ``email-validator`` lib
   (RFC syntax + DNS MX/A lookup). Catches typos, malformed addresses, and
   domains with no mail server.
2. Suppression: never send to an address that previously hard-bounced
   (permanent) or filed a complaint. Bounce/complaint state is recorded on
   email_sequences by the Resend webhook, so this prevents repeat bounces to
   the same address.

Mailbox-level verification (valid domain, dead mailbox) requires a paid
external API and is intentionally out of scope here; add it as a third layer
when a provider/key is available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("gtm.integrations.email_validation")


@dataclass
class EmailValidationResult:
    email: str
    valid: bool
    reason: str  # ok | empty | syntax | undeliverable_domain | suppressed_hard_bounce | suppressed_complaint
    normalized: str | None = None
    checks: dict[str, Any] = field(default_factory=dict)


def _check_syntax_and_domain(email: str) -> tuple[bool, str, str | None]:
    """Return (ok, reason, normalized). Uses email-validator if present."""
    try:
        from email_validator import EmailNotValidError, validate_email
    except ImportError:
        logger.warning("email-validator not installed; skipping syntax/MX check")
        return True, "ok", email
    try:
        info = validate_email(email, check_deliverability=True)
        return True, "ok", info.normalized
    except EmailNotValidError as exc:
        msg = str(exc).lower()
        reason = "undeliverable_domain" if ("domain" in msg or "mx" in msg or "deliver" in msg) else "syntax"
        return False, reason, None


async def _suppression_reason(email: str, *, session) -> str | None:
    """Reason string if this address previously hard-bounced or complained."""
    from sqlalchemy import func, select

    from backend.db.models import Contact, EmailSequence

    rows = (
        await session.execute(
            select(EmailSequence.status, EmailSequence.metadata_json)
            .join(Contact, Contact.id == EmailSequence.contact_id)
            .where(
                func.lower(Contact.email) == email.lower(),
                EmailSequence.status.in_(["bounced", "complained"]),
            )
        )
    ).all()
    for status, meta in rows:
        meta = meta or {}
        if status == "complained":
            return "suppressed_complaint"
        if status == "bounced" and str(meta.get("bounce_type", "")).lower().startswith("perm"):
            return "suppressed_hard_bounce"
    return None


async def validate_for_send(email: str | None, *, session=None) -> EmailValidationResult:
    """Validate an address before sending. Pass a DB session to enable suppression."""
    if not email or not email.strip():
        return EmailValidationResult(email=email or "", valid=False, reason="empty")

    email = email.strip()
    ok, reason, normalized = _check_syntax_and_domain(email)
    checks: dict[str, Any] = {"syntax_domain": ok}
    if not ok:
        return EmailValidationResult(email=email, valid=False, reason=reason, checks=checks)

    if session is not None:
        sup = await _suppression_reason(email, session=session)
        checks["suppression_checked"] = True
        if sup:
            return EmailValidationResult(email=email, valid=False, reason=sup, normalized=normalized, checks=checks)

    return EmailValidationResult(email=email, valid=True, reason="ok", normalized=normalized, checks=checks)
