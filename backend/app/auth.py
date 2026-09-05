from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from hmac import compare_digest

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import DEMO_ADMIN_NAME, DEMO_ADMIN_PASSWORD, DEMO_ADMIN_SESSION_MINUTES


class DemoAuthService:
    """Small in-memory session registry for the hackathon demo, not production auth."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def login(self, name: str, password: str):
        if not compare_digest(name.strip(), DEMO_ADMIN_NAME) or not compare_digest(password, DEMO_ADMIN_PASSWORD):
            return None
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=DEMO_ADMIN_SESSION_MINUTES)
        self._sessions[token] = {"name": DEMO_ADMIN_NAME, "role": "admin", "expires_at": expires_at}
        return {"authenticated": True, "role": "admin", "name": DEMO_ADMIN_NAME, "token": token, "expires_at": expires_at.isoformat()}

    def session(self, token: str):
        session = self._sessions.get(token)
        if not session:
            return None
        if session["expires_at"] <= datetime.now(timezone.utc):
            self._sessions.pop(token, None)
            return None
        return {"authenticated": True, "role": session["role"], "name": session["name"], "expires_at": session["expires_at"].isoformat()}

    def logout(self, token: str):
        return self._sessions.pop(token, None) is not None


auth_service = DemoAuthService()
bearer = HTTPBearer(auto_error=False)


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    session = auth_service.session(credentials.credentials) if credentials and credentials.scheme.lower() == "bearer" else None
    if not session:
        raise HTTPException(401, "Admin authentication required", headers={"WWW-Authenticate": "Bearer"})
    return {**session, "token": credentials.credentials}
