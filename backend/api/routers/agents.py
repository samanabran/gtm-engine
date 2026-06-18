from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_current_user, get_db_session, get_org_id, get_orchestrator
from backend.api.schemas.agents import AgentActionRequest, AgentActionResponse
from backend.api.schemas.auth import UserResponse
from backend.core.exceptions import ValidationError
from backend.core.llm_router import LLMRouter
from backend.db.models import AgentAuditLog
from backend.services import campaign_service, deal_service, lead_service

router = APIRouter(prefix="/agents", tags=["agents"])

_AGENT_DEFS = [
    {
        "id": "icp_agent",
        "name": "ICP Scoring Agent",
        "description": "Scores leads against your ideal customer profile.",
        "triggerLabel": "Score with AI",
    },
    {
        "id": "outbound_agent",
        "name": "Outbound Sequence Agent",
        "description": "Drafts outbound email sequences for a lead.",
        "triggerLabel": "Generate sequence",
    },
    {
        "id": "deal_intel_agent",
        "name": "Deal Intelligence Agent",
        "description": "Analyzes deal risk and recommends next actions.",
        "triggerLabel": "Analyze risk",
    },
    {
        "id": "retention_agent",
        "name": "Retention Agent",
        "description": "Flags churn risk and renewal probability.",
        "triggerLabel": "Run retention check",
    },
]


@router.get("")
async def list_agents(
    org_id: str = Depends(get_org_id),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    result = await session.execute(
        select(
            AgentAuditLog.agent_name,
            func.max(AgentAuditLog.created_at).label("last_run_at"),
            func.avg(AgentAuditLog.latency_ms).label("avg_latency_ms"),
            func.count(AgentAuditLog.id).label("run_count"),
        )
        .where(AgentAuditLog.org_id == UUID(org_id))
        .group_by(AgentAuditLog.agent_name)
    )
    stats_by_agent = {row.agent_name: row for row in result.all()}

    summaries: list[dict] = []
    for agent in _AGENT_DEFS:
        stat = stats_by_agent.get(agent["id"])
        summaries.append(
            {
                "id": agent["id"],
                "name": agent["name"],
                "description": agent["description"],
                "status": "healthy" if stat else "idle",
                "lastRunAt": stat.last_run_at.isoformat() if stat and stat.last_run_at else "",
                "successRate": 1.0 if stat else 0.0,
                "avgLatencyMs": int(stat.avg_latency_ms) if stat and stat.avg_latency_ms else 0,
                "triggerLabel": agent["triggerLabel"],
            }
        )
    return summaries


class TestLLMRequest(BaseModel):
    provider: str = "mock"
    model: str = "mock-model"
    api_key: str | None = None
    temperature: float = 0.2


@router.post("/test-llm")
async def test_llm(
    request: TestLLMRequest,
    _current_user: UserResponse = Depends(get_current_user),
) -> dict:
    probe_router = LLMRouter(provider=request.provider, model=request.model, api_key=request.api_key)
    try:
        response = await probe_router.complete(
            system="You are a connectivity test.",
            user="Reply with exactly one word: pong",
            format="text",
            temperature=request.temperature,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "provider": response.provider, "model": response.model, "content": response.content}


@router.post("/{agent_type}/score", response_model=AgentActionResponse)
async def score_agent(
    agent_type: str,
    request: AgentActionRequest,
    org_id: str = Depends(get_org_id),
    _current_user: UserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AgentActionResponse:
    if agent_type == "icp":
        lead_id = request.payload.get("lead_id")
        if not lead_id:
            raise ValidationError("lead_id is required")
        result = await lead_service.score_lead(org_id, lead_id, session=session)
        return AgentActionResponse(agent_name="icp_agent", status="completed", result=result.model_dump())
    if agent_type == "deal_intel":
        deal_id = request.payload.get("deal_id")
        if not deal_id:
            raise ValidationError("deal_id is required")
        result = await deal_service.analyze_risk(org_id, deal_id, session=session)
        return AgentActionResponse(agent_name="deal_intel_agent", status="completed", result=result.model_dump())
    workflow = await get_orchestrator().run_workflow(agent_type, {"org_id": org_id, **request.payload})
    return AgentActionResponse(agent_name=agent_type, status=workflow.status, result={"steps": workflow.steps})


@router.post("/{agent_type}/generate", response_model=AgentActionResponse)
async def generate_agent(
    agent_type: str,
    request: AgentActionRequest,
    org_id: str = Depends(get_org_id),
    _current_user: UserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AgentActionResponse:
    if agent_type == "outbound":
        lead_id = request.payload.get("lead_id")
        campaign_id = request.payload.get("campaign_id")
        if not lead_id or not campaign_id:
            raise ValidationError("lead_id and campaign_id are required")
        lead = await lead_service.get_lead(org_id, lead_id, session=session)
        result = await campaign_service.generate_outbound(org_id, campaign_id, lead, session=session)
        return AgentActionResponse(
            agent_name="outbound_agent",
            status="completed",
            result={"sequences": [item.model_dump() for item in result]},
        )
    workflow = await get_orchestrator().run_workflow(agent_type, {"org_id": org_id, **request.payload})
    return AgentActionResponse(agent_name=agent_type, status=workflow.status, result={"steps": workflow.steps})


@router.post("/{agent_type}/analyze", response_model=AgentActionResponse)
async def analyze_agent(
    agent_type: str,
    request: AgentActionRequest,
    org_id: str = Depends(get_org_id),
    _current_user: UserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AgentActionResponse:
    workflow = await get_orchestrator().run_workflow(agent_type, {"org_id": org_id, **request.payload})
    return AgentActionResponse(agent_name=agent_type, status=workflow.status, result={"steps": workflow.steps})
