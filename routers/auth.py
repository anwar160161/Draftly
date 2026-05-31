"""
routers/auth.py

Handles the Google OAuth2 login flow and session management.

There are four endpoints here:

    GET /auth/login
        The starting point. Returns a Google authorization URL that the
        client redirects the user to. Nothing sensitive happens here.

    GET /auth/callback
        Google redirects here after the user logs in. This is where the
        real work happens — we exchange the one-time code for tokens,
        look up or create the user in our database, and issue our own JWT.

    GET /auth/me
        A simple "who am I?" endpoint. Useful for the frontend to confirm
        the token is still valid and get the user's profile details.

    POST /auth/logout
        Clears the stored Gmail tokens from the database. The JWT itself
        can't be invalidated (that's a known tradeoff of stateless JWTs),
        so the client should also discard it locally.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth.dependencies import create_access_token, get_current_user
from auth.oauth import (
    encrypt_token, exchange_code, get_authorization_url, get_google_user_info
)
from database import get_db
from models import User, UserPreference
from schemas import AuthURLResponse, TokenResponse, UserProfile, MessageResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get(
    "/login",
    response_model=AuthURLResponse,
    summary="Start Google OAuth2 login",
)
def login():
    """
    Step 1 of the login flow.

    Generates a Google OAuth2 authorization URL and a random state token.
    The client should redirect the user to the authorization_url returned here.
    Google will show a consent screen, and after the user approves, redirect
    them back to /auth/callback with a one-time authorization code.

    The state value is a CSRF protection token — in a production frontend
    you'd store it in a cookie or session and verify it matches when the
    callback comes in.
    """
    url, state = get_authorization_url()
    return AuthURLResponse(authorization_url=url, state=state)


@router.get(
    "/callback",
    response_model=TokenResponse,
    summary="Handle Google OAuth2 callback",
)
def callback(
    code:  str = Query(..., description="One-time authorization code from Google"),
    state: str = Query(None, description="CSRF state token from the login step"),
    db:    Session = Depends(get_db),
):
    """
    Step 2 of the login flow — this is where Google redirects after the user logs in.

    What happens here:
    1. Exchange the one-time code for Gmail access and refresh tokens
    2. Fetch the user's Google profile (name, email, picture)
    3. Create a new user account if this is their first login,
       or update their profile if they've logged in before
    4. Encrypt and save their Gmail tokens to the database
    5. Issue a Draftly JWT they'll use for all future API calls

    The code Google sends is single-use and expires in about 60 seconds,
    so this all needs to happen quickly.

    Returns a bearer token the client should include in the Authorization
    header for all subsequent requests:
        Authorization: Bearer <token>
    """
    # Exchange the authorization code for Gmail OAuth tokens.
    # This makes a network call to Google's token endpoint.
    try:
        token_data = exchange_code(code)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Token exchange failed: {exc}"
        )

    access_token  = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expiry        = token_data.get("expiry")

    # Use the access token to fetch the user's Google profile.
    # We need their Google ID to look them up in our database.
    try:
        profile = get_google_user_info(access_token)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch Google profile: {exc}"
        )

    user_id = profile["id"]
    user = db.get(User, user_id)

    if not user:
        # First time this user has logged in — create their account
        # and set up default preferences (auto tone, no signature).
        user = User(
            id=user_id,
            email=profile["email"],
            name=profile.get("name"),
            picture=profile.get("picture"),
        )
        db.add(user)
        db.add(UserPreference(user_id=user_id))
    else:
        # Returning user — update their profile in case anything changed
        # on the Google side (name change, new profile picture, etc.)
        user.name    = profile.get("name")
        user.picture = profile.get("picture")

    # Encrypt tokens before saving — we never store OAuth tokens in plaintext.
    # We only update the refresh token if Google sent a new one; otherwise we
    # keep the existing one (Google only sends refresh tokens on first consent).
    user.encrypted_access_token  = encrypt_token(access_token)
    user.encrypted_refresh_token = encrypt_token(refresh_token) if refresh_token else user.encrypted_refresh_token
    user.token_expiry             = expiry
    user.updated_at               = datetime.utcnow()

    db.commit()

    # Issue our own JWT — this is what the client will use going forward.
    # We don't pass the Gmail tokens to the client; they stay in the database.
    jwt_token = create_access_token(user_id)
    return TokenResponse(access_token=jwt_token)


@router.get(
    "/me",
    response_model=UserProfile,
    summary="Get current user profile",
)
def me(current_user: User = Depends(get_current_user)):
    """
    Returns the profile of whoever is currently logged in.

    Useful for the client to display the user's name and picture,
    and to verify that a token is still valid before making other calls.
    """
    return current_user


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out and clear Gmail tokens",
)
def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Logs the user out by clearing their stored Gmail tokens.

    After this, any attempt to fetch emails or send drafts will fail because
    the system no longer has valid Gmail credentials for this user. They'll
    need to go through /auth/login again to reconnect.

    Note: the JWT itself remains technically valid until it expires (24 hours),
    since we don't maintain a token blocklist. The client should discard it
    immediately on logout. In a high-security production system you'd want a
    Redis-backed blocklist to invalidate JWTs instantly.
    """
    current_user.encrypted_access_token  = None
    current_user.encrypted_refresh_token = None
    current_user.token_expiry             = None
    db.commit()
    return MessageResponse(message="Logged out successfully.")