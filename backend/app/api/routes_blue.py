from datetime import datetime, timedelta
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import bcrypt
import hashlib

from app.db.database import get_db
from app.db.models import User
from app.core.config import settings

router = APIRouter()

# ---------------------------------------------------------
# Security Utilities (Password Hashing with Pre-Hash)
# ---------------------------------------------------------
def _pre_hash(password: str) -> bytes:
    return hashlib.sha256(password.encode('utf-8')).hexdigest().encode('ascii')


def get_password_hash(password: str) -> str:
    password_bytes = _pre_hash(password)
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = _pre_hash(plain_password)
    return bcrypt.checkpw(password_bytes, hashed_password.encode('utf-8'))


# ---------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

# ---------------------------------------------------------
# SECURE JWT AUTHENTICATION LOGIC
# ---------------------------------------------------------

SECRET_KEY = getattr(settings, "JWT_SECRET")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY not set in configuration")

ALGORITHM = getattr(settings, "JWT_ALGORITHM", "HS256")

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """
    Creates a cryptographically signed JWT token for the Blue Team.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user_secure(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    SECURE: This function strictly verifies the JWT signature.
    Red Team fake tokens (with alg: none) will fail here
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ")[1]

    try:
        # SECURE DECODE: Explicitly verifying signature with SECRET_KEY and ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        # If the signature is wrong, or alg is 'none', it falls here!
        raise HTTPException(status_code=401, detail="Invalid token signature")

    # Check if user exists in DB
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user

# ---------------------------------------------------------
# LOGIN ENDPOINT
# ---------------------------------------------------------
@router.post("/login", response_model=TokenResponse, tags=["Blue Team"])
async def login_blue_team(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticates a Blue Team user and returns a signed JWT.
    """
    # 1. Fetch user from database
    result = await db.execute(select(User).where(User.username == login_data.username))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    # 2. Verify password securely using bcrypt with pre-hash
    if not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    # 3. Check role (Only Blue Team and Admins allowed here)
    if user.role not in ["blue_team", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to access Blue Team panel")

    # 4. Generate token
    access_token_expires = timedelta(minutes=60)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role},
        expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}

# ---------------------------------------------------------
# SECURE TEST ENDPOINT
# ---------------------------------------------------------
@router.get("/me", tags=["Blue Team"])
async def get_my_profile(current_user: User = Depends(get_current_user_secure)):
    """
    A protected endpoint. If a Red Team member tries to use their
    fake 'alg: none' token here, get_current_user_secure will reject it!
    """
    return {
        "username": current_user.username,
        "role": current_user.role,
        "message": "Welcome to the secure Blue Team zone."
    }