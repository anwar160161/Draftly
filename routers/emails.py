"""
routers/emails.py

Read-only access to the user's Gmail inbox.

These endpoints are how you discover what emails need replies. The typical
workflow is:

    GET /emails              → browse your unread inbox, find an email
    GET /emails/{message_id} → read the full email body if needed
    POST /drafts             → generate a reply using the message_id

This router deliberately does not support sending, deleting, or modifying
emails. We only read. All writing goes through the /drafts flow, which
ensures nothing is sent without explicit user approval.

The message_id values returned here are Gmail's native message IDs —
pass them directly to POST /drafts to generate a reply.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from database import get_db
from models import User
from schemas import EmailSummary, EmailDetail
from services import gmail_service

router = APIRouter(prefix="/emails", tags=["Emails"])


@router.get(
    "",
    response_model=list[EmailSummary],
    summary="List recent unread emails from Gmail inbox",
)
def list_emails(
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="How many emails to fetch (1–50, default 10)",
    ),
    current_user: User = Depends(get_current_user),
):
    """
    Fetches your most recent unread emails from Gmail.

    Returns a list of email summaries — enough information to decide which
    emails need replies without fetching full bodies for everything. Each
    summary includes:
    - message_id: pass this to POST /drafts to generate a reply
    - thread_id: the Gmail conversation this email belongs to
    - subject, sender, date: standard email metadata
    - snippet: a short preview of the email body

    If you need the full body of a specific email before generating a draft,
    use GET /emails/{message_id}.

    Returns 502 if there's a problem reaching the Gmail API — this usually
    means the access token has expired and the user needs to log in again.
    """
    try:
        messages = gmail_service.fetch_inbox(current_user, max_results=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch emails from Gmail: {exc}"
        )
    return [EmailSummary(**m) for m in messages]


@router.get(
    "/{message_id}",
    response_model=EmailDetail,
    summary="Get the full body of a specific email",
)
def get_email(
    message_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Fetches the complete content of a single Gmail message.

    Use this when the snippet from GET /emails isn't enough context —
    for example, when the email is long, or when you want to review
    the full thread before generating a draft reply.

    The message_id comes from the list endpoint above. Gmail message IDs
    look something like "19e7cbafc7a52a17".

    Returns 502 if the Gmail API is unreachable or the token has expired.
    Returns the full email including decoded body text, not just the snippet.
    """
    try:
        msg = gmail_service.fetch_message(current_user, message_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch email from Gmail: {exc}"
        )
    return EmailDetail(**msg)