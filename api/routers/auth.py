"""Auth endpoints: login (issue JWT) and me (echo claims)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import (
    User,
    accessible_home_ids,
    authenticate_user,
    create_access_token,
    get_current_user,
)
from ..models import LoginRequest, MeResponse, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    user = await authenticate_user(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(
        access_token=create_access_token(user),
        role=user.role,
        homes=user.homes,
    )


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)):
    # Resolve the concrete home list (fleet roles carry an empty token list).
    homes = await accessible_home_ids(user.user_id, user.role)
    return MeResponse(user_id=user.user_id, role=user.role, homes=homes)
