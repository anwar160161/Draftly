"""
auth/dependencies.py

This file handles everything related to keeping users logged in after they've
completed the Google OAuth flow.

Here's how it works:
- After login, we give the user a JWT (JSON Web Token) — think of it as a 
  temporary ID card that expires after 24 hours.
- Every API request after that includes this token in the Authorization header.
- FastAPI's dependency injection system automatically calls get_current_user()
  on any route that needs authentication, so individual routes don't have to
  worry about token validation themselves.

Why JWT instead of server-side sessions?
- The server stays stateless — no session store needed.
- Works naturally with REST APIs and mobile clients.
- Easy to scale horizontally (any server can validate any token).
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import User

settings = get_settings()

# We use the industry-standard HS256 algorithm for signing tokens.
# The SECRET_KEY in your .env is what makes these tokens tamper-proof —
# anyone who changes the payload without knowing the secret will fail validation.
ALGORITHM = "HS256"

# 24 hours feels right for a tool like this — long enough that users aren't
# constantly re-authenticating, short enough to limit exposure if a token leaks.
TOKEN_EXPIRE_MINUTES = 60 * 24

# This tells FastAPI to look for "Authorization: Bearer <token>" on incoming requests.
bearer_scheme = HTTPBearer()


def create_access_token(user_id: str) -> str:
    """
    Create a signed JWT for the given user.

    The token contains three claims:
    - sub: the user's Google ID (who this token belongs to)
    - exp: when it expires (24 hours from now)
    - iat: when it was issued (useful for debugging)

    The token is signed with SECRET_KEY, so we can verify it hasn't been
    tampered with when it comes back to us.
    """
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """
    Validate a JWT and extract the user ID from it.

    Returns the user ID string if the token is valid and not expired.
    Returns None if the token is invalid, expired, or tampered with.
    We return None instead of raising an exception here so the caller
    can decide what error to show.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        # This catches expired tokens, invalid signatures, malformed tokens —
        # anything that means we can't trust this token.
        return None


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency that validates the Bearer token and returns the logged-in user.

    Any route that needs authentication just adds this to its parameters:
        current_user: User = Depends(get_current_user)

    FastAPI calls this automatically before the route handler runs. If the token
    is missing, invalid, or expired, the request is rejected with a 401 before
    it ever reaches the route.

    We do two checks:
    1. Is the token cryptographically valid? (decode_access_token)
    2. Does the user it points to actually exist in our database?
       (They could have been deleted after the token was issued.)
    """
    token = credentials.credentials
    user_id = decode_access_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, user_id)
    if not user:
        # This can happen if the user was deleted from the database
        # but still has a valid token floating around.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found. Please log in again.",
        )

    return user