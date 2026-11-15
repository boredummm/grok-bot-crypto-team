"""Local bearer auth. Monitor tokens are read-only. Admin tokens change state."""

from __future__ import annotations

from enum import Enum

from fastapi import Header, HTTPException

from app.config import Settings


class Role(str, Enum):
    ADMIN = "admin"
    MONITOR = "monitor"


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
    if token == settings.admin_token:
        return Role.ADMIN
    if token == settings.monitor_token:
        return Role.MONITOR
    raise HTTPException(status_code=401, detail="invalid token")


def require_reader(
    settings: Settings,
    authorization: str | None = Header(default=None),
    x_solswarm_token: str | None = Header(default=None),
) -> Role:
    return resolve_role(settings, authorization, x_solswarm_token)


def require_admin(role: Role) -> Role:
    if role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin authorization required")
    return role
