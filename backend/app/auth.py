"""Local bearer auth. Monitor tokens are read-only, agent tokens scope MCP teammate writes, admin tokens control state."""

from __future__ import annotations

import secrets
from enum import Enum

from fastapi import Header, HTTPException

from app.config import Settings


class Role(str, Enum):
    ADMIN = "admin"
    AGENT = "agent"
    MONITOR = "monitor"


def _match(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def resolve_role(
    settings: Settings,
    authorization: str | None,
    x_solswarm_token: str | None,
) -> Role:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif x_solswarm_token:
        token = x_solswarm_token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    if _match(token, settings.admin_token):
        return Role.ADMIN
    if settings.mcp_token and _match(token, settings.mcp_token):
        return Role.AGENT
    if _match(token, settings.monitor_token):
        return Role.MONITOR
    raise HTTPException(status_code=401, detail="invalid token")


def require_reader(
    settings: Settings,
    authorization: str | None = Header(default=None),
    x_solswarm_token: str | None = Header(default=None),
) -> Role:
    return resolve_role(settings, authorization, x_solswarm_token)


def require_agent_or_admin(role: Role) -> Role:
    if role not in {Role.ADMIN, Role.AGENT}:
        raise HTTPException(status_code=403, detail="agent or admin authorization required")
    return role


def require_admin(role: Role) -> Role:
    if role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin authorization required")
    return role
