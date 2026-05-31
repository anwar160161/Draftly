"""
auth/oauth.py

Handles everything related to Google OAuth2 — the login flow, token exchange,
and keeping OAuth tokens safe in the database.

The overall flow works like this:
    1. get_authorization_url() → send user to Google's login page
    2. User logs in and Google redirects back with a one-time code
    3. exchange_code() → swap that code for real access/refresh tokens
    4. encrypt_token() → encrypt tokens before saving to the database
    5. get_google_user_info() → fetch the user's profile to create their account

Why do we encrypt tokens before saving them?
Gmail OAuth tokens are extremely sensitive — anyone with a valid access token
can read and send emails on behalf of the user. Storing them encrypted means
that even if someone gets access to the database, the tokens are useless
without the encryption key.

One quirk you'll notice: OAUTHLIB_RELAX_TOKEN_SCOPE is set to "1" below.
This is because Google sometimes returns scope names slightly differently than
what we requested (e.g. "userinfo.email" vs "email"). Without this flag, the
OAuth library throws an error even though the scopes are functionally identical.
"""

import base64
import os
import secrets
from typing import Optional

import requests
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from config import get_settings

settings = get_settings()

# Google occasionally returns scope names that differ slightly from what we
# requested — this tells the OAuth library to accept them without complaining.
# Safe to set globally since it only affects scope name comparison, not security.
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"


# ── Token encryption ──────────────────────────────────────────────────────────
# We use Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256) to protect
# OAuth tokens at rest. Fernet guarantees that encrypted data cannot be read
# or tampered with without the key.

def _get_fernet() -> Fernet:
    """
    Build a Fernet encryption instance using the configured key.

    In production, TOKEN_ENCRYPTION_KEY should be a proper 32-byte Fernet key.
    You can generate one with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    In development, if no key is set, we derive one from SECRET_KEY as a
    fallback. This is NOT safe for production — the derived key is predictable
    if SECRET_KEY is ever exposed.
    """
    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        # Derive a deterministic key from SECRET_KEY for local development.
        # Always set TOKEN_ENCRYPTION_KEY explicitly in production.
        raw = settings.SECRET_KEY.encode().ljust(32)[:32]
        key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt_token(token: str) -> str:
    """
    Encrypt an OAuth token string before saving it to the database.
    The result is a base64-encoded string safe to store as text.
    """
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(token: str) -> str:
    """
    Decrypt a previously encrypted OAuth token retrieved from the database.
    Raises an exception if the token has been tampered with or the key is wrong.
    """
    return _get_fernet().decrypt(token.encode()).decode()


# ── OAuth2 flow ───────────────────────────────────────────────────────────────

def create_flow() -> Flow:
    """
    Create a Google OAuth2 Flow object configured with our app credentials.

    The Flow object manages the OAuth2 handshake — it knows which scopes we
    need, where Google should redirect after login, and how to exchange the
    authorization code for tokens.

    We build it from a client config dict rather than a file so there's no
    JSON credentials file to manage or accidentally commit to git.
    """
    client_config = {
        "web": {
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=settings.GMAIL_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


def get_authorization_url() -> tuple[str, str]:
    """
    Generate the Google login URL to redirect the user to.

    Returns a tuple of (authorization_url, state) where:
    - authorization_url: the full Google OAuth consent page URL
    - state: a random token we generate to prevent CSRF attacks

    A few things worth noting:
    - access_type="offline" is what gets us a refresh token, so we can
      make API calls even when the user isn't actively using the app.
    - prompt="consent" forces Google to show the consent screen every time,
      which ensures we always get a fresh refresh token.
    - flow.code_verifier = "" explicitly disables PKCE. Without this,
      the library adds a code challenge to the authorization URL but then
      loses it when we create a new Flow object in exchange_code(), causing
      a "missing code verifier" error from Google.
    """
    flow = create_flow()
    state = secrets.token_urlsafe(32)

    # Disable PKCE — we create a fresh Flow object for each request, so there's
    # nowhere to store the code verifier between the authorization and callback steps.
    flow.code_verifier = ""

    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return url, state


def exchange_code(code: str) -> dict:
    """
    Exchange a one-time authorization code for OAuth tokens.

    Google calls our callback URL with a short-lived code. We swap that
    code here for the actual access token and refresh token we need.

    The code can only be used once and expires quickly (usually 60 seconds),
    so this needs to happen immediately in the callback handler.

    Returns a dict with access_token, refresh_token, expiry, and id_token.
    """
    flow = create_flow()

    # Must match the flow that generated the authorization URL — both need
    # PKCE disabled, otherwise Google rejects the exchange with "missing code verifier".
    flow.code_verifier = ""

    flow.fetch_token(code=code)
    creds = flow.credentials

    return {
        "access_token":  creds.token,
        "refresh_token": creds.refresh_token,
        "expiry":        creds.expiry,
        "id_token":      getattr(creds, "id_token", None),
    }


def build_credentials(access_token: str, refresh_token: Optional[str]) -> Credentials:
    """
    Build a Google Credentials object from stored tokens.

    This is used when we need to make authenticated Google API calls.
    We reconstruct the credentials from what we have stored in the database
    rather than going through the OAuth flow again.
    """
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=settings.GMAIL_SCOPES,
    )


def get_google_user_info(access_token: str) -> dict:
    """
    Fetch the user's Google profile using their access token.

    We use this right after the token exchange to get the user's Google ID,
    email address, name, and profile picture — everything we need to create
    or update their account in our database.

    We call the userinfo endpoint directly with requests rather than using
    the Google API client library, which had connection issues on Windows.
    """
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    resp.raise_for_status()
    return resp.json()