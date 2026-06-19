"""Pre-send email validation to reduce bounces.

Five concrete, dependency-light layers (cheapest first):

1. Syntax + domain deliverability via the open-source ``email-validator`` lib
   (RFC syntax + DNS MX/A lookup). Catches typos, malformed addresses, and
   domains with no mail server.
2. Suppression: never send to an address that previously hard-bounced
   (permanent) or filed a complaint. Bounce/complaint state is recorded on
   email_sequences by the Resend webhook, so this prevents repeat bounces to
   the same address.
3. Contact quality gates: requires an enriched, named, senior contact above
   the ICP score floor. Prevents cold outreach to generic mailboxes and
   Big-4 advisory firms that are not direct buyers.
4. Generic role-based email pattern: blocks info@, uae@, no-reply@, etc.
5. Big-4 advisory firm blocklist: KPMG, PwC, EY, Deloitte, BDO, Mazars,
   Grant Thornton, Moore Stephens, RSM, Baker Tilly, Crowe. These are
   potential partners, not buyers.

Mailbox-level verification (valid domain, dead mailbox) requires a paid
external API and is intentionally out of scope here; add it as a sixth
layer when a provider/key is available.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("gtm.integrations.email_validation")


# Minimum ICP score required to send outreach. Tuned to the Agentic AI
# campaign's calibrated score distribution: contacts scoring < 0.5 had
# bounce rates >15% in the first dispatch. Keep this in sync with the
# contact-enrichment prompt that produces the score.
MIN_ICP_SCORE = 0.5

# Local-parts that are catch-all / role-based mailboxes. We never send
# cold outreach to these — they bounce, get filtered, or are unread.
GENERIC_LOCAL_PARTS = frozenset(
    {
        "info",
        "uae",
        "uae-info",
        "contact",
        "hello",
        "admin",
        "office",
        "support",
        "sales",
        "marketing",
        "noreply",
        "no-reply",
        "postmaster",
        "abuse",
        "press",
        "media",
        "careers",
        "jobs",
        "hr",
        "enquiries",
        "enquiry",
        "inquiries",
        "inquiry",
        "feedback",
        "team",
        "general",
        "reception",
    }
)

# Big-4 + major advisory / accounting firms. We are a sell-side (Odoo
# implementation + AI agents) targeting finance and ops at mid-market
# companies. These firms are *partners* / *advisors*, not direct buyers
# of our delivery services. Cold-emailing them burns sender reputation.
ADVISORY_FIRM_DOMAINS = frozenset(
    {
        # Big 4
        "kpmg.com",
        "pwc.com",
        "ey.com",
        "deloitte.com",
        # Mid-tier / regional Big-4-adjacent
        "bdo.com",
        "bdo.global",
        "mazars.com",
        "mazars.ae",
        "grantthornton.com",
        "moorestephens.com",
        "rsm.global",
        "rsmus.com",
        "bakertilly.com",
        "crowe.com",
        # Common UAE advisory aliases
        "ae.ey.com",
        "ae.pwc.com",
        "ae.deloitte.com",
        "ae.kpmg.com",
    }
)


@dataclass
class EmailValidationResult:
    email: str
    valid: bool
    reason: str  # ok | empty | syntax | undeliverable_domain | suppressed_hard_bounce | suppressed_complaint | generic_role_email | missing_contact_identity | unenriched_contact | low_icp_score | advisory_firm_blocked
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


def _check_generic_role_email(email: str) -> bool:
    """True if the local-part is a known catch-all role (info@, uae@, etc.)."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower().strip()
    # Strip +tag suffixes (info+2024@x.com -> info)
    if "+" in local:
        local = local.split("+", 1)[0]
    return local in GENERIC_LOCAL_PARTS


def _extract_domain(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].lower().strip()


def _is_advisory_firm(email: str) -> bool:
    """True if the email domain matches a known advisory / Big-4 firm."""
    domain = _extract_domain(email)
    if not domain:
        return False
    # Exact match
    if domain in ADVISORY_FIRM_DOMAINS:
        return True
    # Suffix match: a.ey.com -> ey.com; uae.deloitte.com -> deloitte.com
    for blocked in ADVISORY_FIRM_DOMAINS:
        if domain.endswith("." + blocked) or domain == blocked:
            return True
    return False


def _check_contact_identity(contact) -> tuple[bool, str]:
    """True if contact has a name or job title. Generic mailboxes without
    an identified human are not viable cold-outreach targets."""
    if contact is None:
        # No contact object -> we can't verify identity, so let it pass
        # (the existing 'no_contact' guard already covers missing contact).
        return True, ""
    first = (getattr(contact, "first_name", None) or "").strip()
    last = (getattr(contact, "last_name", None) or "").strip()
    title = (getattr(contact, "title", None) or "").strip()
    has_name = bool(first or last)
    has_title = bool(title)
    if not has_name and not has_title:
        return False, "missing_contact_identity"
    return True, ""


def _check_enrichment(contact) -> tuple[bool, str]:
    """True if the contact has been enriched. Unenriched contacts have
    no company fit data and historically bounce at 2-3x the rate."""
    if contact is None:
        return True, ""
    enrichment = (getattr(contact, "enrichment_status", None) or "").strip().lower()
    # Treat None / empty / "pending" / "failed" as not-enriched
    if enrichment in ("", "pending", "failed", "none"):
        return False, "unenriched_contact"
    return True, ""


def _check_icp_score(contact) -> tuple[bool, str]:
    """True if the contact's ICP score is at or above the floor.
    Unscored contacts (None) are treated as low-fit for cold outreach."""
    if contact is None:
        return True, ""
    score = getattr(contact, "icp_score", None)
    if score is None:
        return False, "low_icp_score"
    try:
        if float(score) < MIN_ICP_SCORE:
            return False, "low_icp_score"
    except (TypeError, ValueError):
        return False, "low_icp_score"
    return True, ""


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


async def validate_for_send(
    email: str | None,
    *,
    session=None,
    contact=None,
) -> EmailValidationResult:
    """Validate an address before sending. Pass a DB session to enable
    suppression checks, and a Contact to enable contact-quality gates.

    Returns EmailValidationResult. The `reason` field on invalid results
    is one of:
      - empty
      - syntax / undeliverable_domain
      - suppressed_hard_bounce / suppressed_complaint
      - generic_role_email
      - missing_contact_identity
      - unenriched_contact
      - low_icp_score
      - advisory_firm_blocked
    """
    if not email or not email.strip():
        return EmailValidationResult(email=email or "", valid=False, reason="empty")

    email = email.strip()
    checks: dict[str, Any] = {}

    # Layer 4: generic role-based email (cheapest, no DB)
    if _check_generic_role_email(email):
        checks["generic_role_email"] = "blocked"
        return EmailValidationResult(
            email=email,
            valid=False,
            reason="generic_role_email",
            checks=checks,
        )

    # Layer 5: Big-4 / advisory firm blocklist (cheapest, no DB)
    if _is_advisory_firm(email):
        checks["advisory_firm"] = "blocked"
        return EmailValidationResult(
            email=email,
            valid=False,
            reason="advisory_firm_blocked",
            checks=checks,
        )

    # Layer 3: contact-quality gates (needs the contact object)
    if contact is not None:
        ok, reason = _check_contact_identity(contact)
        checks["contact_identity"] = "ok" if ok else reason
        if not ok:
            return EmailValidationResult(
                email=email,
                valid=False,
                reason=reason,
                checks=checks,
            )

        ok, reason = _check_enrichment(contact)
        checks["enrichment"] = "ok" if ok else reason
        if not ok:
            return EmailValidationResult(
                email=email,
                valid=False,
                reason=reason,
                checks=checks,
            )

        ok, reason = _check_icp_score(contact)
        checks["icp_score"] = (
            "ok" if ok else f"{reason}:{getattr(contact, 'icp_score', None)}"
        )
        if not ok:
            return EmailValidationResult(
                email=email,
                valid=False,
                reason=reason,
                checks=checks,
            )

    # Layer 1: syntax + MX / deliverability
    ok, reason, normalized = _check_syntax_and_domain(email)
    checks["syntax_domain"] = ok
    if not ok:
        return EmailValidationResult(email=email, valid=False, reason=reason, checks=checks)

    # Layer 2: suppression (previous hard-bounce / complaint)
    if session is not None:
        sup = await _suppression_reason(email, session=session)
        checks["suppression_checked"] = True
        if sup:
            return EmailValidationResult(
                email=email,
                valid=False,
                reason=sup,
                normalized=normalized,
                checks=checks,
            )

    return EmailValidationResult(
        email=email,
        valid=True,
        reason="ok",
        normalized=normalized,
        checks=checks,
    )
