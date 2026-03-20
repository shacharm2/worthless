"""FastAPI dependency injection for the proxy service."""

from __future__ import annotations

import httpx
from fastapi import Request

from worthless.proxy.config import ProxySettings
from worthless.proxy.rules import RulesEngine
from worthless.storage.repository import ShardRepository


def get_repo(request: Request) -> ShardRepository:
    """Return the ShardRepository from app state."""
    return request.app.state.repo


def get_httpx_client(request: Request) -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient from app state."""
    return request.app.state.httpx_client


def get_rules_engine(request: Request) -> RulesEngine:
    """Return the RulesEngine from app state."""
    return request.app.state.rules_engine


def get_settings(request: Request) -> ProxySettings:
    """Return the ProxySettings from app state."""
    return request.app.state.settings
