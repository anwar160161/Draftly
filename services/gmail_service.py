"""
Draftly - Gmail Service

This module provides a lightweight wrapper around the Gmail API.

Features:
    - Fetch inbox emails
    - Fetch sent emails
    - Fetch a specific email
    - Send replies
    - Send standalone emails

Uses the Gmail REST API via the requests library instead of
google-api-python-client for better compatibility across environments.
"""

import base64
from email.mime.text import MIMEText
from typing import Optional

import requests as req

from auth.oauth import decrypt_token
from config import get_settings
from models import User
import re

settings = get_settings()


# ============================================================================
# Helper Functions
# ============================================================================

def _get_headers(user: User) -> dict:
    """
    Generate Gmail API authorization headers.

    Decrypts the user's stored OAuth access token and formats it
    as a Bearer token required by Gmail API requests.

    Args:
        user (User): Authenticated user.

    Returns:
        dict: Authorization headers.
    """
    access_token = decrypt_token(user.encrypted_access_token)

    return {
        "Authorization": f"Bearer {access_token}"
    }


def _decode_body(payload: dict) -> str:
    """
    Recursively extract plain-text content from a Gmail message.

    Gmail messages may contain nested multipart sections.
    This function traverses the payload tree and returns
    the first text/plain content it finds.

    Args:
        payload (dict): Gmail message payload.

    Returns:
        str: Decoded email body.
    """
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")

        return base64.urlsafe_b64decode(
            data + "=="
        ).decode("utf-8", errors="replace")

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _decode_body(part)

            if text:
                return text

    return ""

def _extract_last_message_id(references: str) -> str | None:
    """
    Extract the last Message-ID from a References header string.

    Used as a fallback when the Message-ID header is missing from
    the email — which happens with some email clients and servers.

    The References header looks like:
        19e7e040b18756cf <CAMLcut...@mail.gmail.com>

    We want the last angle-bracketed value since that's the most
    recent message in the thread.

    Args:
        references (str): Raw References header value.

    Returns:
        str | None: Last message ID found, or None if not found.
    """
    if not references:
        return None
    matches = re.findall(r'<[^>]+>', references)
    return matches[-1] if matches else None

def _parse_message(msg: dict) -> dict:
    """
    Convert a raw Gmail API response into a simplified structure.

    Extracts commonly used email metadata including subject,
    sender, date, body, and snippet.

    The rfc_message_id is the standard email Message-ID header
    (looks like <CABc123@mail.gmail.com>) — this is what email
    clients use for threading, not Gmail's internal message ID.

    Args:
        msg (dict): Raw Gmail message.

    Returns:
        dict: Parsed message details.
    """
    headers = {
        h["name"].lower(): h["value"]
        for h in msg["payload"].get("headers", [])
    }

    return {
            "message_id":     msg["id"],
            "thread_id":      msg["threadId"],
            "subject":        headers.get("subject"),
            "sender":         headers.get("from"),
            "date":           headers.get("date"),
            "snippet":        msg.get("snippet", ""),
            "body":           _decode_body(msg["payload"]),
            "rfc_message_id": headers.get("message-id") or _extract_last_message_id(headers.get("references", "")),
            "references":     headers.get("references"),
        }

# ============================================================================
# Public Gmail API Functions
# ============================================================================

def fetch_inbox(user: User, max_results: int = None) -> list[dict]:
    """
    Fetch recent emails from the user's inbox.

    Args:
        user (User): Authenticated user.
        max_results (int, optional): Maximum number of emails to retrieve.

    Returns:
        list[dict]: Parsed inbox messages.

    Raises:
        requests.HTTPError: If Gmail API request fails.
    """
    limit = max_results or settings.DEFAULT_EMAIL_FETCH_LIMIT
    headers = _get_headers(user)

    resp = req.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers,
        params={
            "labelIds": "INBOX",
            "maxResults": limit,
        },
    )

    resp.raise_for_status()

    messages = resp.json().get("messages", [])
    result = []

    for message in messages:
        msg_resp = req.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message['id']}",
            headers=headers,
            params={"format": "full"},
        )

        msg_resp.raise_for_status()

        result.append(
            _parse_message(msg_resp.json())
        )

    return result


def fetch_message(user: User, message_id: str) -> dict:
    """
    Retrieve a specific Gmail message.

    Args:
        user (User): Authenticated user.
        message_id (str): Gmail message identifier.

    Returns:
        dict: Parsed email details.

    Raises:
        requests.HTTPError: If retrieval fails.
    """
    headers = _get_headers(user)

    resp = req.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        headers=headers,
        params={"format": "full"},
    )

    resp.raise_for_status()

    return _parse_message(resp.json())


def fetch_sent_emails(
    user: User,
    max_results: int = None,
) -> list[dict]:
    """
    Fetch recently sent emails.

    Used for writing-style analysis and reply generation.

    Args:
        user (User): Authenticated user.
        max_results (int, optional): Maximum emails to retrieve.

    Returns:
        list[dict]: Parsed sent messages.

    Raises:
        requests.HTTPError: If Gmail API request fails.
    """
    limit = max_results or settings.DEFAULT_SENT_FETCH_LIMIT
    headers = _get_headers(user)

    resp = req.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers,
        params={
            "labelIds": "SENT",
            "maxResults": limit,
        },
    )

    resp.raise_for_status()

    messages = resp.json().get("messages", [])
    result = []

    for message in messages:
        msg_resp = req.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message['id']}",
            headers=headers,
            params={"format": "full"},
        )

        msg_resp.raise_for_status()

        result.append(
            _parse_message(msg_resp.json())
        )

    return result


def send_reply(
    user: User,
    thread_id: str,
    to: str,
    subject: str,
    body: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> str:
    """
    Send a reply within an existing Gmail conversation thread.

    Maintains email threading using the Gmail thread ID and
    optional message references.

    Args:
        user (User): Authenticated user.
        thread_id (str): Gmail thread identifier.
        to (str): Recipient email address.
        subject (str): Original email subject.
        body (str): Reply content.
        in_reply_to (str, optional): Original message ID.
        references (str, optional): Full reference chain from original email.

    Returns:
        str: Gmail ID of the sent message.

    Raises:
        requests.HTTPError: If sending fails.
    """
    headers = _get_headers(user)

    mime = MIMEText(body, "plain")
    mime["To"] = to
    mime["Subject"] = (
        subject
        if subject.startswith("Re:")
        else f"Re: {subject}"
    )

    if in_reply_to:
        # RFC 5322 requires message IDs to be wrapped in angle brackets
        formatted_in_reply_to = f"<{in_reply_to}>" if not in_reply_to.startswith("<") else in_reply_to
        mime["In-Reply-To"] = formatted_in_reply_to
        
        # Construct References header with full chain
        if references:
            # Format all message IDs with angle brackets
            formatted_refs = " ".join(
                f"<{ref}>" if not ref.startswith("<") else ref
                for ref in references.split()
            )
            # Append the in_reply_to message ID to the existing reference chain
            mime["References"] = f"{formatted_refs} {formatted_in_reply_to}"
        else:
            # If no existing references, just use the in_reply_to
            mime["References"] = formatted_in_reply_to

    raw = base64.urlsafe_b64encode(
        mime.as_bytes()
    ).decode()

    resp = req.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers=headers,
        json={
            "raw": raw,
            "threadId": thread_id,
        },
    )

    resp.raise_for_status()

    return resp.json()["id"]


def send_plain_email(
    user: User,
    to: str,
    subject: str,
    body: str,
) -> str:
    """
    Send a standalone email.

    Commonly used for system notifications,
    alerts, and automated emails.

    Args:
        user (User): Authenticated user.
        to (str): Recipient email address.
        subject (str): Email subject.
        body (str): Email body content.

    Returns:
        str: Gmail ID of the sent message.

    Raises:
        requests.HTTPError: If sending fails.
    """
    headers = _get_headers(user)

    mime = MIMEText(body, "plain")
    mime["To"] = to
    mime["Subject"] = subject

    raw = base64.urlsafe_b64encode(
        mime.as_bytes()
    ).decode()

    resp = req.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers=headers,
        json={"raw": raw},
    )

    resp.raise_for_status()

    return resp.json()["id"]