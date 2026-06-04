"""
Dashboard API (FastAPI) — read layer over pezerr_db + guarded writer to
control_actions. This module wires the app: DB pool lifecycle, CORS, and
routers. See docs/DASHBOARD_DESIGN.md §7 for the endpoint spec.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .control_bus import control_bus
from .db import CONFIG_DIR, db
from .routers import (
    admin,
    analytics,
    auth,
    control,
    dr,
    health,
    homes,
    live,
    reports,
    scenarios,
    telemetry,
)

DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _cors_origins() -> list[str]:
    """cors_origins from config/api_config.json if present, else dev defaults."""
    path = os.path.join(CONFIG_DIR, "api_config.json")
    if os.path.exists(path):
        with open(path) as f:
            origins = json.load(f).get("cors_origins")
            if origins:
                return origins
    return DEFAULT_CORS_ORIGINS


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await control_bus.connect()
    yield
    await control_bus.disconnect()
    await db.disconnect()


app = FastAPI(
    title="Pezzrr Dashboard API",
    version="0.1.0",
    description="Read API over the smart-home energy fleet (pezerr_db).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(homes.router)
app.include_router(telemetry.router)
app.include_router(live.router)
app.include_router(control.router)
app.include_router(dr.router)
app.include_router(scenarios.router)
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(health.router)
app.include_router(admin.router)


@app.get("/api/v1/health", tags=["health"])
async def health():
    return {"status": "ok"}
