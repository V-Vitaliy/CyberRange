from datetime import datetime, timedelta
import jwt
import bcrypt
import hashlib
from fastapi import Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.db.models import User
from app.core.config import settings

# ---------------------------------------------------------
# Password Hashing Utilities
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

# ---------------------------------------------------------
# JWT Utilities
# ---------------------------------------------------------
SECRET_KEY = getattr(settings, "JWT_SECRET", "super-secret-blue-team-key")
ALGORITHM = getattr(settings, "JWT_ALGORITHM", "HS256")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY not set in configuration")

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Creates a cryptographically signed JWT token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# =========================================================
# SECURE AUTHENTICATION (Blue Team)
# =========================================================
async def get_current_user_secure(
    authorization: str = Header(None),
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

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user

# =========================================================
# VULNERABLE AUTHENTICATION (Red Team)
# =========================================================
async def get_current_user_vulnerable(authorization: str = Header(None)) -> dict:
    """
    VULNERABLE: Intentionally bypasses signature validation.
    Allows attackers to forge tokens (e.g., alg: none) to elevate privileges.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.split(" ")[1]

    try:
        # Intentionally vulnerable: verify_signature=False
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")