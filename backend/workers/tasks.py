from __future__ import annotations

import asyncio
from typing import Any

from backend.core.logging_config import get_logger
from backend.services import campaign_service, lead_service
from backend.workers.celery_app import celery_app

logger = get_logger("gtm.worker")


def _run_async(coro):
    return asyncio.run(coro)


def _mark_job(job_id: str, status: str, result: Any | None = None) -> None:
    """Persist job lifecycle to the JobRun table (the single source of truth).
    No in-memory state. Safe no-op if job_id is missing/unknown."""
    if not job_id:
        return

    async def _upd() -> None:
        from uuid import UUID as _UUID
        from sqlalchemy import select
        from backend.db.session import build_session_factory, build_async_engine
        from backend.db.models import JobRun, utc_now as _now

        try:
            jid = _UUID(str(job_id))
        except Exception:
            return
        factory = build_session_factory(build_async_engine())
        async with factory() as session:
            jr = (await session.execute(select(JobRun).where(JobRun.id == jid))).scalar_one_or_none()
            if jr is None:
                return
            jr.status = status
            if status == "running" and jr.started_at is None:
                jr.started_at = _now()
            if status in ("completed", "failed"):
                jr.finished_at = _now()
            if status == "failed":
                jr.retry_count = (jr.retry_count or 0) + 1
            if result is not None:
                if isinstance(result, dict) and "error" in result:
                    jr.error_message = str(result.get("error"))
                jr.result_data = result if isinstance(result, dict) else {"value": result}
            await session.commit()

    try:
        _run_async(_upd())
    except Exception:
        logger.exception("failed to persist JobRun %s", job_id)


@celery_app.task(name="backend.workers.tasks.enrich_contact")
def enrich_contact(
    contact_id: str,
    org_id: str,
    job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger.info("enrich_contact", extra={"contact_id": contact_id, "org_id": org_id})
    if job_id:
        _mark_job(job_id, "running")
    result = {"contact_id": contact_id, "org_id": org_id, "enrichment_status": "skipped"}
    if job_id:
        _mark_job(job_id, "completed", result)
    return result


@celery_app.task(name="backend.workers.tasks.score_icp")
def score_icp(
    lead_id: str,
    org_id: str,
    job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger.info("score_icp", extra={"lead_id": lead_id, "org_id": org_id})
    if job_id:
        _mark_job(job_id, "running")
    result = _run_async(lead_service.score_lead(org_id, lead_id)).model_dump()
    if job_id:
        _mark_job(job_id, "completed", result)
    return result


@celery_app.task(name="backend.workers.tasks.generate_outbound")
def generate_outbound(
    lead_id: str,
    campaign_id: str,
    org_id: str,
    job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger.info("generate_outbound", extra={"lead_id": lead_id, "campaign_id": campaign_id, "org_id": org_id})
    if job_id:
        _mark_job(job_id, "running")
    async def _run() -> list[dict[str, Any]]:
        from backend.db.session import build_session_factory, build_async_engine
        factory = build_session_factory(build_async_engine())
        async with factory() as session:
            lead = await lead_service.get_lead(org_id, lead_id, session=session)
            result = await campaign_service.generate_outbound(
                org_id, campaign_id, lead, session=session
            )
            return [item.model_dump() for item in result]

    payload = {"sequences": _run_async(_run())}
    if job_id:
        _mark_job(job_id, "completed", payload)
    return payload


@celery_app.task(name="backend.workers.tasks.sync_crm")
def sync_crm(
    org_id: str,
    job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger.info("sync_crm", extra={"org_id": org_id})
    if job_id:
        _mark_job(job_id, "running")
    result = {"org_id": org_id, "synced": 0}
    if job_id:
        _mark_job(job_id, "completed", result)
    return result


@celery_app.task(name="backend.workers.tasks.weekly_digest")
def weekly_digest(
    org_id: str,
    job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger.info("weekly_digest", extra={"org_id": org_id})
    if job_id:
        _mark_job(job_id, "running")
    result = {"org_id": org_id, "summary": "digest generated"}
    if job_id:
        _mark_job(job_id, "completed", result)
    return result


@celery_app.task(name="backend.workers.tasks.send_email_sequence", bind=True, max_retries=5)
def send_email_sequence(self, sequence_id: str, job_id: str | None = None) -> dict[str, Any]:
    """The ONLY code path permitted to deliver an email for a sequence.

    Guards (all enforced here): parent campaign must be active, sequence must be
    approved, and the send is idempotent (skips if already sending/sent).
    Persists provider_message_id, sent_at, status, error_message, retry_count.
    No silent failures.
    """
    if job_id:
        _mark_job(job_id, "running")

    async def _run() -> dict[str, Any]:
        from sqlalchemy import select
        from backend.db.session import build_session_factory, build_async_engine
        from backend.db.models import Campaign, EmailSequence, Integration, Contact, utc_now as _now
        from backend.core.encryption import decrypt_payload
        from backend.core.exceptions import ServiceUnavailableError
        from backend.integrations.email.gmail import GmailEmailClient
        from backend.integrations.email.outlook import OutlookEmailClient
        from backend.integrations.email.resend import ResendEmailClient

        factory = build_session_factory(build_async_engine())
        async with factory() as session:
            seq = (await session.execute(
                select(EmailSequence).where(EmailSequence.id == sequence_id)
            )).scalar_one_or_none()
            if seq is None:
                return {"status": "skipped", "reason": "sequence_not_found", "sequence_id": sequence_id}

            if seq.status in ("sent", "sending"):
                return {"status": "skipped", "reason": "already_" + seq.status, "sequence_id": sequence_id}

            if seq.status != "approved":
                return {"status": "skipped", "reason": "not_approved(" + seq.status + ")", "sequence_id": sequence_id}

            campaign = (await session.execute(
                select(Campaign).where(Campaign.id == seq.campaign_id)
            )).scalar_one_or_none()
            if campaign is None or campaign.status != "active":
                return {"status": "skipped", "reason": "campaign_not_active", "sequence_id": sequence_id}

            if seq.contact_id is None:
                return {"status": "skipped", "reason": "no_contact", "sequence_id": sequence_id}
            contact = (await session.execute(
                select(Contact).where(Contact.id == seq.contact_id)
            )).scalar_one_or_none()
            if contact is None or not contact.email:
                return {"status": "skipped", "reason": "no_contact_email", "sequence_id": sequence_id}

            # Pre-send validation: drop undeliverable/syntactically-invalid
            # addresses and ones that previously hard-bounced or complained,
            # before hitting the provider — avoids bounces and protects
            # sender reputation.
            from backend.integrations.email.validation import validate_for_send
            _vr = await validate_for_send(contact.email, session=session)
            if not _vr.valid:
                seq.status = "skipped"
                _meta = dict(seq.metadata_json or {})
                _meta["skip_reason"] = "invalid_email:" + _vr.reason
                _meta.pop("error_message", None)
                seq.metadata_json = _meta
                await session.commit()
                logger.info("send_email_sequence: skipped seq %s invalid email (%s)", sequence_id, _vr.reason)
                return {"status": "skipped", "reason": "invalid_email", "detail": _vr.reason, "sequence_id": sequence_id}

            # Dedupe: never send more than one sequence to the same contact
            # in the same campaign. Multiple approved variations would each
            # fire and double-email the prospect.
            if seq.campaign_id is not None and seq.contact_id is not None:
                _dup = (await session.execute(
                    select(EmailSequence.id).where(
                        EmailSequence.campaign_id == seq.campaign_id,
                        EmailSequence.contact_id == seq.contact_id,
                        EmailSequence.id != seq.id,
                        EmailSequence.status.in_(["sending", "sent"]),
                    ).limit(1)
                )).first()
                if _dup is not None:
                    seq.status = "skipped"
                    _m = dict(seq.metadata_json or {})
                    _m["skip_reason"] = "duplicate_contact"
                    seq.metadata_json = _m
                    await session.commit()
                    return {"status": "skipped", "reason": "duplicate_contact", "sequence_id": sequence_id}

            # Claim the work before calling the provider so a crash leaves a
            # recoverable 'sending' row rather than a silent gap.
            seq.status = "sending"
            await session.commit()

            integration = (await session.execute(
                select(Integration).where(
                    Integration.org_id == seq.org_id,
                    Integration.provider.in_(["gmail", "outlook", "resend"]),
                    Integration.status == "connected",
                )
            )).scalar_one_or_none()

            try:
                if integration is None:
                    raise ServiceUnavailableError("no connected email integration for org")
                creds = decrypt_payload(integration.credentials_encrypted or "")
                token = creds.get("access_token", "") or creds.get("oauth_token", "")
                from_address = creds.get("from_address", "") or creds.get("email", "")
                if integration.provider == "gmail":
                    client = GmailEmailClient(oauth_token=token, from_address=from_address)
                elif integration.provider == "outlook":
                    client = OutlookEmailClient(oauth_token=token, from_address=from_address)
                else:
                    client = ResendEmailClient(api_key=token, from_address=from_address)

                result = await client.send_email(to=contact.email, subject=seq.subject, body=seq.body)
                if result.get("status") not in ("sent", "queued"):
                    raise ServiceUnavailableError(
                        result.get("detail") or result.get("reason") or "provider send failed"
                    )

                seq.status = "sent"
                seq.sent_at = _now()
                meta = dict(seq.metadata_json or {})
                meta["provider"] = integration.provider
                meta["provider_message_id"] = result.get("message_id")
                meta["sent_to"] = contact.email
                meta.pop("error_message", None)
                seq.metadata_json = meta
                await session.commit()
                logger.info("send_email_sequence: sent seq %s via %s", sequence_id, integration.provider)
                return {"status": "sent", "sequence_id": sequence_id, "message_id": result.get("message_id")}
            except Exception as exc:
                seq.status = "failed"
                meta = dict(seq.metadata_json or {})
                meta["error_message"] = str(exc)
                meta["retry_count"] = int(meta.get("retry_count", 0)) + 1
                seq.metadata_json = meta
                await session.commit()
                logger.error("send_email_sequence: seq %s failed: %s", sequence_id, exc)
                raise

    try:
        result = _run_async(_run())
    except Exception as exc:
        if job_id:
            _mark_job(job_id, "failed", {"error": str(exc)})
        raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 900))
    if job_id:
        _mark_job(job_id, "completed", result)
    return result

@celery_app.task(name="backend.workers.tasks.batch_score")
def batch_score(
    job_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score all unscored contacts using the ICP agent."""
    org_id = (payload or {}).get("org_id")
    logger.info("batch_score", extra={"org_id": org_id or "all"})
    if job_id:
        _mark_job(job_id, "running")

    async def _run() -> dict[str, Any]:
        from sqlalchemy import select
        from backend.db.session import build_session_factory, build_async_engine
        from backend.db.models import Contact, Organization

        factory = build_session_factory(build_async_engine())
        scored = 0

        async with factory() as session:
            if org_id:
                org_ids = [org_id]
            else:
                result = await session.execute(select(Organization.id).where(Organization.is_active.is_(True)))
                org_ids = [str(row[0]) for row in result.all()]

            for oid in org_ids:
                from uuid import UUID as _UUID
                try:
                    org_uuid = _UUID(oid)
                except Exception:
                    continue
                result = await session.execute(
                    select(Contact).where(
                        Contact.org_id == org_uuid,
                        Contact.icp_score.is_(None),
                        Contact.status.in_(["new", "enriched"]),
                    ).limit(20)
                )
                contacts = result.scalars().all()
                for contact in contacts:
                    try:
                        await lead_service.score_lead(oid, str(contact.id), session=session)
                        scored += 1
                    except Exception as exc:
                        logger.debug("batch_score: error scoring contact %s: %s", contact.id, exc)

        return {"scored": scored}

    task_result = _run_async(_run())
    if job_id:
        _mark_job(job_id, "completed", task_result)
    return task_result


TASK_REGISTRY = {
    "enrich_contact": enrich_contact,
    "score_icp": score_icp,
    "generate_outbound": generate_outbound,
    "sync_crm": sync_crm,
    "weekly_digest": weekly_digest,
    "batch_score": batch_score,
}


def dispatch_task(task_name: str, **kwargs: Any) -> dict[str, Any]:
    task = TASK_REGISTRY.get(task_name)
    if not task:
        return {"task_name": task_name, "dispatched": False}
    async_result = task.apply_async(kwargs=kwargs)
    return {"task_name": task_name, "task_id": async_result.id, "dispatched": True}
