import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from app.core.config import settings
from app.db.database import get_db
from app.db.models import User
from app.db.repository import UserRepository, GameSessionRepository, ensure_uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------
#  Hashing Utilities
# ---------------------------------------------------------
def _pre_hash(password: str) -> bytes:
    """Pre-hash with SHA-256 to bypass bcrypt's 72-byte limit."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest().encode('ascii')

def get_password_hash(password: str) -> str:
    """Creates a bcrypt password hash."""
    password_bytes = _pre_hash(password)
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies the password directly via bcrypt."""
    password_bytes = _pre_hash(plain_password)
    return bcrypt.checkpw(password_bytes, hashed_password.encode('utf-8'))

def hash_ctf_flag(flag_value: str) -> str:
    """Hashes a CTF flag value using SHA-256 for secure database storage and comparison."""
    return hashlib.sha256(flag_value.encode('utf-8')).hexdigest()

# ---------------------------------------------------------
# JWT Utilities
# ---------------------------------------------------------
SECRET_KEY = getattr(settings, "JWT_SECRET", "super-secret-blue-team-key")
ALGORITHM = getattr(settings, "JWT_ALGORITHM", "HS256")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY not set in configuration")

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
    authorization: str | None = Depends(get_bearer_authorization),
    db: AsyncSession = Depends(get_db)
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


async def get_authorized_session(db, session_id, lab_instance_id):
    try:
        session = await GameSessionRepository.get_by_id(db, session_id)
        lab_id = ensure_uuid(lab_instance_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid session or lab ID")
    if session is None or session.lab_instance_id != lab_id:
        raise HTTPException(status_code=404, detail="Game session not found")
    return session


async def get_current_user_vulnerable(
    authorization: str | None = Depends(get_bearer_authorization),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Allow role forgery only within the authenticated student's laboratory."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        scope = jwt.decode(
            payload.get("lab_scope", ""), SECRET_KEY,
            algorithms=[ALGORITHM], audience="red-lab-scope",
            options={"require": ["sub", "id", "lab_instance_id", "exp"]},
        )
        user = await UserRepository.get_by_id(db, ensure_uuid(scope["id"]))
        if (user is None or user.role != "red_team"
                or user.username != scope["sub"]
                or str(user.lab_instance_id) != scope["lab_instance_id"]):
            raise ValueError("Invalid lab scope; please re-login")
        for key in ("sub", "id", "lab_instance_id"):
            if payload.get(key) != scope[key]:
                raise ValueError("Token identity does not match its lab scope")
        session = await GameSessionRepository.get_by_lab_instance_id(db, user.lab_instance_id)
        if session is None:
            raise ValueError("Game session not found")
        if session.jwt_filter_enabled:
            payload = jwt.decode(
                token, SECRET_KEY, algorithms=[ALGORITHM],
                options={"require": ["exp", "sub", "id", "lab_instance_id"]},
            )
            if payload.get("role") != user.role:
                raise ValueError("Invalid token role")
        return {
            "sub": user.username,
            "id": str(user.id),
            "role": payload.get("role"),
            "lab_instance_id": str(user.lab_instance_id),
        }
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token; please re-login") from exc
