"""
Auth & RBAC (docs/DASHBOARD_DESIGN.md §9).

JWT (HS256) carries {user_id, role, homes}. `homes` is the explicit access
list for viewer/operator and is empty for fleet_analyst/admin, which means
"all homes". The require() dependency factory enforces a minimum role rank
and, when given a path param, that the requested home is in scope.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from .db import CONFIG_DIR, db

ALGORITHM = "HS256"

ROLE_RANK = {"viewer": 1, "operator": 2, "fleet_analyst": 3, "admin": 4}
ALL_HOMES_ROLES = {"fleet_analyst", "admin"}
DISPATCH_ROLES = {"operator", "admin"}

_config_cache: Optional[dict] = None


def _config() -> dict:
    global _config_cache
    if _config_cache is None:
        path = os.path.join(CONFIG_DIR, "api_config.json")
        if not os.path.exists(path):
            raise RuntimeError(
                "config/api_config.json missing (needs jwt_secret, jwt_ttl_min)"
            )
        with open(path) as f:
            _config_cache = json.load(f)
    return _config_cache


# =====================================================================
# Password hashing
# =====================================================================
def hash_password(plain: str) -> str:
    # bcrypt caps input at 72 bytes; truncate defensively.
    return bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
    except (ValueError, TypeError):
        return False


# =====================================================================
# JWT
# =====================================================================
class User(BaseModel):
    user_id: int
    role: str
    homes: list[int]  # explicit list; empty => all homes for fleet roles


def create_access_token(user: User) -> str:
    cfg = _config()
    ttl_min = int(cfg.get("jwt_ttl_min", 720))
    expire = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)
    claims = {
        "user_id": user.user_id,
        "role": user.role,
        "homes": user.homes,
        "exp": expire,
    }
    return jwt.encode(claims, cfg["jwt_secret"], algorithm=ALGORITHM)


def decode_token(token: str) -> User:
    cfg = _config()
    try:
        payload = jwt.decode(token, cfg["jwt_secret"], algorithms=[ALGORITHM])
        return User(
            user_id=payload["user_id"],
            role=payload["role"],
            homes=payload.get("homes", []),
        )
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# =====================================================================
# Authentication (login)
# =====================================================================
async def authenticate_user(username: str, password: str) -> Optional[User]:
    row = await db.fetchrow(
        """SELECT user_id, password_hash, role
           FROM app_users WHERE username = $1 AND is_active""",
        username,
    )
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    homes = await accessible_home_ids(row["user_id"], row["role"])
    # Fleet roles carry an empty list (= all homes) in the token.
    token_homes = [] if row["role"] in ALL_HOMES_ROLES else homes
    return User(user_id=row["user_id"], role=row["role"], homes=token_homes)


async def accessible_home_ids(user_id: int, role: str) -> list[int]:
    """Home IDs a user may access. Fleet roles get every home."""
    if role in ALL_HOMES_ROLES:
        rows = await db.fetch("SELECT home_id FROM homes ORDER BY home_id")
    else:
        rows = await db.fetch(
            "SELECT home_id FROM user_home_access WHERE user_id = $1 ORDER BY home_id",
            user_id,
        )
    return [r["home_id"] for r in rows]


# =====================================================================
# Dependencies
# =====================================================================
_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> User:
    return decode_token(creds.credentials)


def _has_home_scope(user: User, home_id: int) -> bool:
    return user.role in ALL_HOMES_ROLES or home_id in user.homes


def require(role_min: str = "viewer", home_param: Optional[str] = None):
    """Dependency: enforce a minimum role and (optionally) home-scope.

    home_param names a path parameter (e.g. "home_id"); the requested home
    must be in the caller's scope or the request is rejected with 403.
    """
    min_rank = ROLE_RANK[role_min]

    async def dep(request: Request, user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < min_rank:
            raise HTTPException(status_code=403, detail="Insufficient role")
        if home_param is not None:
            raw = request.path_params.get(home_param)
            if raw is not None and not _has_home_scope(user, int(raw)):
                raise HTTPException(status_code=403, detail="Home not in scope")
        return user

    return dep


def require_dispatch():
    """Dependency: only operator/admin may dispatch control (§9)."""

    async def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in DISPATCH_ROLES:
            raise HTTPException(status_code=403, detail="Dispatch requires operator or admin")
        return user

    return dep
