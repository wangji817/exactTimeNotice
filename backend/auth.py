from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

SESSION_COOKIE = "sgz_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class SessionInfo:
    token: str
    nickname: str
    openid: str
    avatar: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0


class AuthManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionInfo] = {}

    def _purge(self) -> None:
        now = time.time()
        expired = [
            tok
            for tok, s in self._sessions.items()
            if s.expires_at and now > s.expires_at
        ]
        for tok in expired:
            self._sessions.pop(tok, None)

    def _create_session_locked(
        self, nickname: str, openid: str, avatar: str = ""
    ) -> SessionInfo:
        token = secrets.token_urlsafe(32)
        now = time.time()
        session = SessionInfo(
            token=token,
            nickname=nickname,
            openid=openid,
            avatar=avatar,
            created_at=now,
            expires_at=now + SESSION_TTL_SECONDS,
        )
        self._sessions[token] = session
        return session

    def create_session(
        self, nickname: str, openid: str, avatar: str = ""
    ) -> SessionInfo:
        with self._lock:
            self._purge()
            return self._create_session_locked(nickname, openid, avatar)

    def get_session(self, token: str | None) -> SessionInfo | None:
        if not token:
            return None
        with self._lock:
            self._purge()
            session = self._sessions.get(token)
            if not session:
                return None
            if session.expires_at and time.time() > session.expires_at:
                self._sessions.pop(token, None)
                return None
            return session

    def destroy_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


auth_manager = AuthManager()
