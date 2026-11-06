"""Passwords, JWT tokens and the FastAPI dependencies that authenticate users."""

import asyncio
import functools
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.auth.repository import UserRepository
from app.core.config import settings
from app.core.redis_client import redis_client
from app.db.database import get_db
from app.db.ids import ensure_uuid
from app.db.models import User
from app.defense.service import enforce_login_attempt_limit
from app.labs.service import get_session_for_lab
from app.siem.service import log_failed_login, log_jwt_manipulation_attempt


# ---------------------------------------------------------
#  Hashing Utilities
# ---------------------------------------------------------
def _pre_hash(password: str) -> bytes:
    """Pre-hash with SHA-256 to bypass bcrypt's 72-byte limit."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def get_password_hash(password: str) -> str:
    """Creates a bcrypt password hash."""
    password_bytes = _pre_hash(password)
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies the password directly via bcrypt."""
    password_bytes = _pre_hash(plain_password)
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))


@functools.cache
def dummy_password_hash() -> str:
    """Hash of a random password, as costly to check as a real one (Login Hardening)."""
    return get_password_hash(secrets.token_urlsafe(32))


# ---------------------------------------------------------
# JWT Utilities
# ---------------------------------------------------------
# Validated at application startup by require_strong_jwt_secret.
SECRET_KEY = settings.JWT_SECRET
ALGORITHM = settings.JWT_ALGORITHM

bearer_auth = HTTPBearer(
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    description="Paste access_token only; Swagger adds the Bearer prefix.",
    auto_error=False,
)


async def get_bearer_authorization(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
) -> str | None:
    # Declare HTTP authentication in OpenAPI: Swagger ignores Authorization
    # when it is declared as an ordinary header parameter. JWT verification
    # and the intentional Red Team behavior remain in the existing functions.
    if credentials is None:
        return None
    return f"Bearer {credentials.credentials}"


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Creates a cryptographically signed JWT token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta if expires_delta else timedelta(minutes=15))
    to_encode.update({"exp": expire})
    if data.get("role") == "red_team":
        # Keep the educational outer JWT forgeable, but authenticate its lab boundary.
        scope = {key: data[key] for key in ("sub", "id", "lab_instance_id")}
        scope.update({"exp": expire, "aud": "red-lab-scope"})
        to_encode["lab_scope"] = jwt.encode(scope, SECRET_KEY, algorithm=ALGORITHM)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# =========================================================
# SECURE AUTHENTICATION (Blue Team)
# =========================================================
async def get_current_user_secure(
    authorization: str | None = Depends(get_bearer_authorization), db: AsyncSession = Depends(get_db)
) -> User:
    """
    SECURE: Strictly verifies JWT signature and retrieves the user.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ")[1]

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token signature")

    user = await UserRepository.get_by_username(db, username)

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


# =========================================================
# VULNERABLE AUTHENTICATION (Red Team)
# =========================================================
async def get_blue_team_user(user: User = Depends(get_current_user_secure)) -> User:
    if user.role not in ("blue_team", "admin"):
        raise HTTPException(status_code=403, detail="Blue Team access required")
    return user


async def get_current_user_vulnerable(
    authorization: str | None = Depends(get_bearer_authorization),
    db: AsyncSession = Depends(get_db),
    connection: HTTPConnection = None,
) -> dict:
    """Allow role forgery only within the authenticated student's laboratory.

    A claimed role that differs from the stored one is logged in the student's own lab,
    whether JWT Strict Mode lets it through or rejects it.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        scope = jwt.decode(
            payload.get("lab_scope", ""),
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience="red-lab-scope",
            options={"require": ["sub", "id", "lab_instance_id", "exp"]},
        )
        user = await UserRepository.get_by_id(db, ensure_uuid(scope["id"]))
        if (
            user is None
            or user.role != "red_team"
            or user.username != scope["sub"]
            or str(user.lab_instance_id) != scope["lab_instance_id"]
        ):
            raise ValueError("Invalid lab scope; please re-login")
        for key in ("sub", "id", "lab_instance_id"):
            if payload.get(key) != scope[key]:
                raise ValueError("Token identity does not match its lab scope")
        session = await get_session_for_lab(db, user.lab_instance_id)
        if session is None:
            raise ValueError("Game session not found")
        claimed_role = payload.get("role")

        async def log_forgery(blocked):
            if claimed_role != user.role:
                await log_jwt_manipulation_attempt(
                    lab_instance_id=str(user.lab_instance_id),
                    provided_role=claimed_role,
                    source_ip=connection.client.host if connection and connection.client else "unknown",
                    user_id=str(user.id),
                    blocked=blocked,
                )

        if session.jwt_filter_enabled:
            try:
                payload = jwt.decode(
                    token,
                    SECRET_KEY,
                    algorithms=[ALGORITHM],
                    options={"require": ["exp", "sub", "id", "lab_instance_id"]},
                )
                if payload.get("role") != user.role:
                    raise ValueError("Invalid token role")
            except (jwt.InvalidTokenError, ValueError):
                await log_forgery(blocked=True)
                raise
        else:
            await log_forgery(blocked=False)
        return {
            "sub": user.username,
            "id": str(user.id),
            "role": payload.get("role"),
            "lab_instance_id": str(user.lab_instance_id),
        }
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token; please re-login") from exc


async def get_or_create_hidden_user(db: AsyncSession, username: str, role: str, lab_instance_id) -> User:
    """A lab user nobody can log in as: its random password is never stored or shown."""
    user = await UserRepository.get_by_username(db, username)
    if user is not None:
        if user.role != role or user.lab_instance_id != ensure_uuid(lab_instance_id):
            raise ValueError("The username belongs to a user with a different role or lab.")
        return user
    password_hash = await asyncio.to_thread(get_password_hash, secrets.token_urlsafe(32))
    return await UserRepository.add(
        db,
        id=uuid.uuid4(),
        username=username,
        role=role,
        lab_instance_id=ensure_uuid(lab_instance_id),
        password_hash=password_hash,
    )


async def authenticate(
    db: AsyncSession, username: str, password: str, lab_instance_id: uuid.UUID, *, source_ip: str, portal: str
) -> User | None:
    """Return the lab's user when the password matches; bcrypt runs off the event loop.

    Without Login Hardening an unknown username skips bcrypt and answers faster:
    the intended EX-07 timing difference. Failed logins are logged in the lab.
    """
    session = await get_session_for_lab(db, lab_instance_id)
    if session is None:
        return None
    if session.login_hardening_enabled:
        await enforce_login_attempt_limit(redis_client, session, source_ip)
    user = await UserRepository.get_by_username_in_lab(db, username, lab_instance_id)
    if user is not None:
        if await asyncio.to_thread(verify_password, password, user.password_hash):
            return user
    elif session.login_hardening_enabled:
        # The dummy hash is created in the thread too: its first use runs bcrypt twice.
        await asyncio.to_thread(lambda: verify_password(password, dummy_password_hash()))
    await log_failed_login(lab_instance_id=str(lab_instance_id), username=username, source_ip=source_ip, portal=portal)
    return None
