from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.exceptions import AuthenticationError
from backend.services import user_service

router = APIRouter(tags=["events"])

_HEARTBEAT_INTERVAL = int(os.getenv("SSE_HEARTBEAT_SECONDS", "30"))
_CHANNEL_PREFIX = "gtm:events"
_bearer = HTTPBearer(auto_error=False)


def _channel(org_id: str) -> str:
    return f"{_CHANNEL_PREFIX}:{org_id}"


async def _redis_event_stream(org_id: str, request: Request) -> AsyncGenerator[str, None]:
    yield ": connected\n\n"

    redis_client = None
    pubsub = None

    try:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is not None:
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(_channel(org_id))
    except Exception:
        redis_client = None
        pubsub = None

    if pubsub is not None:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=_HEARTBEAT_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue

                if message and message.get("type") == "message":
                    try:
                        event = json.loads(message["data"])
                        yield f"event: {event.get('type', 'message')}\n"
                        yield f"data: {json.dumps(event)}\n\n"
                    except (json.JSONDecodeError, TypeError):
                        pass
        finally:
            try:
                await pubsub.unsubscribe(_channel(org_id))
                await pubsub.close()
            except Exception:
                pass
    else:
        from backend.services.state import get_state
        state = get_state()
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(state.events.get(), timeout=_HEARTBEAT_INTERVAL)
                yield f"event: {event.get('type', 'message')}\n"
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"


@router.get("/events/agent-status")
async def agent_status_events(
    request: Request,
    token: str | None = Query(default=None),
) -> StreamingResponse:
    """
    SSE stream for real-time agent status. Accepts Bearer token via
    Authorization header or ?token= query param (required for EventSource).
    """
    raw_token: str | None = None

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        raw_token = auth_header[7:]
    elif token:
        raw_token = token

    if not raw_token:
        raise AuthenticationError("Missing token")

    current_user = await user_service.get_current_user(raw_token)

    return StreamingResponse(
        _redis_event_stream(current_user.org_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def publish_event(redis_client, org_id: str, event_type: str, payload: dict) -> None:
    event = {"type": event_type, **payload}
    try:
        if redis_client is not None:
            await redis_client.publish(_channel(org_id), json.dumps(event))
        else:
            from backend.services.state import get_state
            get_state().publish_event(event_type, payload)
    except Exception:
        pass
