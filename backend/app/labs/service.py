"""Game sessions of a lab instance; other domains use these functions, not the repository."""

import base64
import binascii
import hashlib
import hmac
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.ids import ensure_uuid
from app.db.models import GameSession
from app.labs.repository import GameSessionRepository, LabSecretRepository


async def get_session(db: AsyncSession, session_id: str | uuid.UUID, *, for_update: bool = False) -> GameSession | None:
    return await GameSessionRepository.get_by_id(db, session_id, for_update=for_update)


async def get_session_for_lab(db: AsyncSession, lab_instance_id: str | uuid.UUID) -> GameSession | None:
    return await GameSessionRepository.get_by_lab_instance_id(db, lab_instance_id)


async def get_authorized_session(db, session_id, lab_instance_id):
    try:
        session = await GameSessionRepository.get_by_id(db, session_id)
        lab_id = ensure_uuid(lab_instance_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid session or lab ID")
    if session is None or session.lab_instance_id != lab_id:
        raise HTTPException(status_code=404, detail="Game session not found")
    return session


async def get_exercise_secret(db: AsyncSession, lab_instance_id: str | uuid.UUID, exercise: str) -> str | None:
    """Plaintext of an exercise secret the lab needs at runtime (EX-03), or None."""
    secret = await LabSecretRepository.get(db, lab_instance_id, exercise)
    return secret.value if secret else None


def _login_code_mac(lab: uuid.UUID) -> bytes:
    message = b"cyberrange-lab-login-code:" + lab.bytes
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), message, hashlib.sha256).digest()[:16]


def lab_login_code(lab_instance_id: str | uuid.UUID) -> str:
    """The code a lab's students log in with, issued by admin_setup.

    Lab IDs are not secret (the SQLi exercise can list every lab's database role),
    so login accepts only this signed code, never a bare lab ID.
    """
    lab = ensure_uuid(lab_instance_id)
    return base64.urlsafe_b64encode(lab.bytes + _login_code_mac(lab)).rstrip(b"=").decode("ascii")


def lab_from_login_code(code: str) -> uuid.UUID | None:
    """The lab a genuine login code names, or None for forged or malformed codes."""
    try:
        raw = base64.b64decode(code.encode("ascii") + b"=" * (-len(code) % 4), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return None
    if len(raw) != 32:
        return None
    lab = uuid.UUID(bytes=raw[:16])
    return lab if hmac.compare_digest(raw[16:], _login_code_mac(lab)) else None
