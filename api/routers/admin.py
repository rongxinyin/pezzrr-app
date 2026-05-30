"""
Admin user management (docs/DASHBOARD_DESIGN.md §13.8).

Admin-only CRUD over app_users and the per-user home-access grants in
user_home_access. Passwords are bcrypt-hashed via auth.hash_password and never
returned. Role values are validated against ROLE_RANK. Home-access rows only
matter for viewer/operator (fleet roles see every home regardless), but we
store them as given so a later role change behaves predictably.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import ROLE_RANK, User, hash_password, require
from ..db import db
from ..models import AdminUser, CreateUserRequest, UpdateUserRequest

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_admin = require("admin")


async def _homes_for(user_id: int) -> list[int]:
    rows = await db.fetch(
        "SELECT home_id FROM user_home_access WHERE user_id = $1 ORDER BY home_id",
        user_id,
    )
    return [r["home_id"] for r in rows]


async def _set_home_access(user_id: int, homes: list[int]) -> None:
    await db.execute("DELETE FROM user_home_access WHERE user_id = $1", user_id)
    for hid in dict.fromkeys(homes):  # de-dupe, preserve order
        await db.execute(
            """INSERT INTO user_home_access (user_id, home_id) VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            user_id, hid,
        )


async def _load(user_id: int) -> AdminUser:
    r = await db.fetchrow(
        "SELECT user_id, username, role, is_active, created_at FROM app_users WHERE user_id = $1",
        user_id,
    )
    return AdminUser(
        user_id=r["user_id"], username=r["username"], role=r["role"],
        is_active=r["is_active"], created_at=r["created_at"],
        homes=await _homes_for(user_id),
    )


@router.get("/users", response_model=list[AdminUser])
async def list_users(_: User = Depends(_admin)):
    rows = await db.fetch(
        "SELECT user_id, username, role, is_active, created_at FROM app_users ORDER BY user_id"
    )
    access = await db.fetch("SELECT user_id, home_id FROM user_home_access ORDER BY home_id")
    by_user: dict[int, list[int]] = {}
    for a in access:
        by_user.setdefault(a["user_id"], []).append(a["home_id"])
    return [
        AdminUser(
            user_id=r["user_id"], username=r["username"], role=r["role"],
            is_active=r["is_active"], created_at=r["created_at"],
            homes=by_user.get(r["user_id"], []),
        )
        for r in rows
    ]


@router.post("/users", response_model=AdminUser, status_code=201)
async def create_user(body: CreateUserRequest, _: User = Depends(_admin)):
    if body.role not in ROLE_RANK:
        raise HTTPException(status_code=422, detail="Invalid role")
    if not body.username.strip() or not body.password:
        raise HTTPException(status_code=422, detail="username and password required")
    exists = await db.fetchval("SELECT 1 FROM app_users WHERE username = $1", body.username)
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")

    user_id = await db.fetchval(
        """INSERT INTO app_users (username, password_hash, role, is_active)
           VALUES ($1, $2, $3, $4) RETURNING user_id""",
        body.username, hash_password(body.password), body.role, body.is_active,
    )
    await _set_home_access(user_id, body.homes)
    return await _load(user_id)


@router.patch("/users/{user_id}", response_model=AdminUser)
async def update_user(user_id: int, body: UpdateUserRequest, admin: User = Depends(_admin)):
    cur = await db.fetchrow("SELECT role, is_active FROM app_users WHERE user_id = $1", user_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None and body.role not in ROLE_RANK:
        raise HTTPException(status_code=422, detail="Invalid role")

    # Guard against an admin locking themselves out / demoting the last admin.
    demoting = (body.role is not None and body.role != "admin") or body.is_active is False
    if cur["role"] == "admin" and demoting:
        others = await db.fetchval(
            "SELECT count(*) FROM app_users WHERE role = 'admin' AND is_active AND user_id <> $1",
            user_id,
        )
        if others == 0:
            raise HTTPException(status_code=409, detail="Cannot demote the last active admin")

    if body.role is not None:
        await db.execute("UPDATE app_users SET role = $2 WHERE user_id = $1", user_id, body.role)
    if body.is_active is not None:
        await db.execute(
            "UPDATE app_users SET is_active = $2 WHERE user_id = $1", user_id, body.is_active
        )
    if body.password:
        await db.execute(
            "UPDATE app_users SET password_hash = $2 WHERE user_id = $1",
            user_id, hash_password(body.password),
        )
    if body.homes is not None:
        await _set_home_access(user_id, body.homes)
    return await _load(user_id)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin: User = Depends(_admin)):
    cur = await db.fetchrow("SELECT role FROM app_users WHERE user_id = $1", user_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == admin.user_id:
        raise HTTPException(status_code=409, detail="Cannot delete your own account")
    if cur["role"] == "admin":
        others = await db.fetchval(
            "SELECT count(*) FROM app_users WHERE role = 'admin' AND is_active AND user_id <> $1",
            user_id,
        )
        if others == 0:
            raise HTTPException(status_code=409, detail="Cannot delete the last active admin")
    await db.execute("DELETE FROM app_users WHERE user_id = $1", user_id)
