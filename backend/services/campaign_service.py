from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.campaigns import CampaignCreate, CampaignResponse, CampaignUpdate, SequenceResponse
from backend.api.schemas.leads import LeadResponse
from backend.core.context_builder import ContextBuilder, build_context_builder
from backend.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from backend.core.llm_router import LLMRouter, build_llm_router
from backend.core.prompt_manager import PromptManager, build_prompt_manager
from backend.db.models import Campaign
from backend.db.repositories.audit_repo import AuditRepository
from backend.db.repositories.campaign_repo import CampaignRepository

from .base import BaseService


def _campaign_to_response(campaign: Campaign) -> CampaignResponse:
    return CampaignResponse(
        id=str(campaign.id),
        org_id=str(campaign.org_id),
        name=campaign.name,
        tone=campaign.tone or "professional",
        product_value_prop=campaign.value_prop,
        brand_voice=campaign.brand_voice,
        target_icp=campaign.icp_filters or {},
        metadata={},
        status=campaign.status,
        active=campaign.is_active,
        sequences=[],
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


@dataclass(slots=True)
class CampaignService(BaseService):
    llm_router: LLMRouter = field(default_factory=build_llm_router)
    context_builder: ContextBuilder = field(default_factory=build_context_builder)
    prompt_manager: PromptManager = field(default_factory=build_prompt_manager)

    async def create_campaign(self, org_id: str, data: CampaignCreate, *, session: AsyncSession) -> CampaignResponse:
        repo = CampaignRepository(session)
        payload = {
            "name": data.name,
            "tone": data.tone,
            "value_prop": data.product_value_prop,
            "brand_voice": data.brand_voice,
            "icp_filters": data.target_icp,
            "is_active": True,
        }
        try:
            campaign = await repo.create(org_id=UUID(org_id), data=payload)
            await session.commit()
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        return _campaign_to_response(campaign)

    async def list_campaigns(self, org_id: str, *, session: AsyncSession) -> list[CampaignResponse]:
        repo = CampaignRepository(session)
        try:
            campaigns = await repo.list(org_id=UUID(org_id))
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        return [_campaign_to_response(c) for c in campaigns]

    async def get_campaign(self, org_id: str, campaign_id: str, *, session: AsyncSession) -> CampaignResponse:
        repo = CampaignRepository(session)
        try:
            campaign = await repo.get(org_id=UUID(org_id), object_id=UUID(campaign_id))
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        if campaign is None:
            raise NotFoundError("Campaign not found")
        return _campaign_to_response(campaign)

    async def update_campaign(
        self,
        org_id: str,
        campaign_id: str,
        data: CampaignUpdate,
        *,
        session: AsyncSession,
    ) -> CampaignResponse:
        repo = CampaignRepository(session)
        updates = data.model_dump(exclude_none=True)
        # Map schema fields → model fields
        if "product_value_prop" in updates:
            updates["value_prop"] = updates.pop("product_value_prop")
        if "target_icp" in updates:
            updates["icp_filters"] = updates.pop("target_icp")
        updates.pop("metadata", None)
        try:
            campaign = await repo.update_by_id(org_id=UUID(org_id), object_id=UUID(campaign_id), data=updates)
            await session.commit()
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        if campaign is None:
            raise NotFoundError("Campaign not found")
        return _campaign_to_response(campaign)

    _TRANSITIONS = {
        "activate": ({"draft", "paused"}, "active"),
        "pause": ({"active"}, "paused"),
        "resume": ({"paused"}, "active"),
        "complete": ({"draft", "active", "paused"}, "completed"),
    }

    async def set_lifecycle(
        self,
        org_id: str,
        campaign_id: str,
        action: str,
        *,
        session: AsyncSession,
    ) -> CampaignResponse:
        """Apply a campaign lifecycle transition (FSM). Illegal transitions and
        any mutation of a completed campaign raise ConflictError (HTTP 409)."""
        repo = CampaignRepository(session)
        try:
            campaign = await repo.get(org_id=UUID(org_id), object_id=UUID(campaign_id))
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        if campaign is None:
            raise NotFoundError("Campaign not found")

        if campaign.status == "completed":
            raise ConflictError("Campaign is completed and immutable")
        allowed_from, target = self._TRANSITIONS[action]
        if campaign.status not in allowed_from:
            raise ConflictError(f"Cannot {action} a campaign in '{campaign.status}' state")

        campaign.status = target
        try:
            await session.commit()
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc

        # Activating (or resuming) a campaign dispatches its already-approved,
        # unsent sequences through the single send path. Sends are idempotent.
        if target == "active":
            await self._dispatch_campaign_sends(org_id, campaign_id, session=session)

        return _campaign_to_response(campaign)

    async def _dispatch_campaign_sends(
        self,
        org_id: str,
        campaign_id: str,
        *,
        session: AsyncSession,
    ) -> int:
        from sqlalchemy import select
        from backend.db.models import EmailSequence
        from backend.workers.tasks import send_email_sequence

        rows = await session.execute(
            select(EmailSequence.id, EmailSequence.contact_id, EmailSequence.variation_rank).where(
                EmailSequence.org_id == UUID(org_id),
                EmailSequence.campaign_id == UUID(campaign_id),
                EmailSequence.status == "approved",
            ).order_by(EmailSequence.contact_id, EmailSequence.variation_rank)
        )
        # One send per contact: keep the lowest-rank approved variation.
        seen_contacts: set = set()
        dispatched = 0
        for seq_id, contact_id, _rank in rows.all():
            if contact_id is not None and contact_id in seen_contacts:
                continue
            seen_contacts.add(contact_id)
            send_email_sequence.delay(sequence_id=str(seq_id))
            dispatched += 1
        return dispatched

    async def generate_outbound(
        self,
        org_id: str,
        campaign_id: str,
        lead: LeadResponse,
        *,
        session: AsyncSession,
    ) -> list[SequenceResponse]:
        campaign = await self.get_campaign(org_id, campaign_id, session=session)
        if campaign.status == "completed":
            raise ConflictError("Campaign is completed and immutable")
        context = self.context_builder.build_full_outbound_context(
            contact=lead.model_dump(),
            campaign=campaign.model_dump(),
            enrichment=lead.enrichment_data,
        )
        response = await self.llm_router.complete(
            system=self.prompt_manager.load("outbound_personalization"),
            user=context,
            format="json",
            metadata={"org_id": org_id, "agent_name": "outbound_agent"},
        )
        # Tolerant parse: LLMs often wrap JSON in ``` fences or add prose.
        # Never raise here — fall back to {} so the default variation is used.
        raw_json = (response.content or "").strip()
        import re as _re
        if raw_json.startswith("```"):
            raw_json = _re.sub(r"^```[a-zA-Z0-9]*\s*", "", raw_json)
            raw_json = _re.sub(r"\s*```$", "", raw_json).strip()
        try:
            payload = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            _m = _re.search(r"\{.*\}", raw_json, _re.DOTALL)
            try:
                payload = json.loads(_m.group(0)) if _m else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        variations: list[dict[str, Any]] = payload.get("variations", [])
        if not variations:
            variations = [
                {
                    "subject": "Quick question",
                    "body": "Would you be open to a short chat?",
                    "hook_type": "direct",
                    "confidence": 0.7,
                }
            ]
        repo = CampaignRepository(session)
        sequences: list[SequenceResponse] = []
        for rank, variation in enumerate(variations[:3], start=1):
            seq_data: dict[str, Any] = {
                "campaign_id": UUID(campaign_id),
                "contact_id": UUID(lead.id) if lead.id else None,
                "variation_rank": rank,
                "subject": variation.get("subject", f"Variation {rank}"),
                "body": variation.get("body", ""),
                "hook_type": variation.get("hook_type"),
                "confidence": float(variation.get("confidence", 0.0)),
                "status": "pending_approval",
                "metadata_json": {"campaign_id": campaign_id, "lead_id": lead.id},
            }
            try:
                seq = await repo.create_sequence(org_id=UUID(org_id), data=seq_data)
            except Exception as exc:
                raise ServiceUnavailableError(str(exc)) from exc
            sequences.append(
                SequenceResponse(
                    id=str(seq.id),
                    campaign_id=campaign_id,
                    lead_id=lead.id,
                    variation_rank=rank,
                    subject=seq.subject,
                    body=seq.body,
                    hook_type=seq.hook_type,
                    confidence=seq.confidence or 0.0,
                    status=seq.status,
                    created_at=seq.created_at,
                )
            )
        await AuditRepository(session).append(
            org_id=UUID(org_id),
            data={
                "agent_name": "outbound_agent",
                "operation": "generate_outbound",
                "raw_response": f"Generated {len(sequences)} sequence variant(s) for lead {lead.id}",
            },
        )
        try:
            await session.commit()
        except Exception as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        self.state.publish_event(
            "outbound_generated",
            {"org_id": org_id, "campaign_id": campaign_id, "lead_id": lead.id},
        )
        return sequences
