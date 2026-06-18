from __future__ import annotations

import os

from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_current_user, get_db_session, get_org_id
from backend.api.schemas.auth import (
    AuthSessionResponse,
    LoginRequest,
    LogoutResponse,
    RegisterRequest,
    UserResponse,
)
from backend.core.exceptions import AuthenticationError
from backend.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "gtm_refresh_token"
_REFRESH_MAX_AGE = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")) * 24 * 60 * 60
_COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN") or None


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_REFRESH_MAX_AGE,
        domain=_COOKIE_DOMAIN,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        domain=_COOKIE_DOMAIN,
        path="/",
    )


@router.post("/register", response_model=AuthSessionResponse)
async def register(
    request: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> AuthSessionResponse:
    result = await user_service.register(
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        org_name=request.org_name,
        role=request.role,
        session=session,
    )
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return result


@router.post("/login", response_model=AuthSessionResponse)
async def login(
    request: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> AuthSessionResponse:
    result = await user_service.login(
        email=request.email, password=request.password, session=session
    )
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return result


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh(
    response: Response,
    cookie_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
    session: AsyncSession = Depends(get_db_session),
) -> AuthSessionResponse:
    if not cookie_token:
        raise AuthenticationError("No refresh token")
    result = await user_service.refresh(cookie_token, session=session)
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return result


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    current_user: UserResponse = Depends(get_current_user),
) -> LogoutResponse:
    await user_service.logout(current_user.id)
    _clear_refresh_cookie(response)
    return LogoutResponse()


@router.get("/users", response_model=list[UserResponse])
async def list_team(
    org_id: str = Depends(get_org_id),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserResponse]:
    return await user_service.list_users(org_id, session=session)


class InviteRequest(BaseModel):
    email: EmailStr
    full_name: str | None = None
    role: str = "member"


@router.post("/invite", response_model=UserResponse)
async def invite_user(
    request: InviteRequest,
    org_id: str = Depends(get_org_id),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    return await user_service.invite_user(
        org_id, request.email, request.role, request.full_name, session=session
    )


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_role(
    user_id: str,
    role: str,
    org_id: str = Depends(get_org_id),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    return await user_service.update_user_role(org_id, user_id, role, session=session)
