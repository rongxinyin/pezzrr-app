"""
asyncpg connection pool over pezerr_db.

DSN is built from config/data_analytics_config.json -> database, the same
source data_collectors/config.py uses (host, port, database_name, username,
password). The pool is created on FastAPI startup and closed on shutdown.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import asyncpg

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")


def get_db_config() -> dict:
    with open(os.path.join(CONFIG_DIR, "data_analytics_config.json")) as f:
        return json.load(f)["database"]


def get_connect_kwargs() -> dict:
    db = get_db_config()
    return {
        "host": db["host"],
        "port": db["port"],
        "database": db["database_name"],
        "user": db["username"],
        "password": db["password"],
    }


class Database:
    """Holds the process-wide asyncpg pool."""

    def __init__(self) -> None:
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                **get_connect_kwargs(), min_size=1, max_size=10
            )

    async def disconnect(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)


db = Database()
