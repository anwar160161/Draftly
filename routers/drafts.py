"""
routers/drafts.py

The heart of Draftly — everything related to creating, reviewing, and sending
AI-generated email drafts lives here.

The draft lifecycle always follows this sequence:

    POST /drafts              → creates a draft in "pending" status
    GET  /drafts/{id}         → user reviews the generated draft
    POST /drafts/{id}/approve → user approves (with or without edits)
         OR
    POST /drafts/{id}/reject  → user rejects it (won't be sent)
    POST /drafts/{id}/send    → sends the approved draft via Gmail

The system enforces this order strictly. You cannot send a pending draft —
it must be approved first. This is intentional: the whole point of Draftly
is that the AI suggests and the human decides. Nothing goes out without
explicit approval.

All the actual business logic lives in services/draft_service.py.
These routes are intentionally thin — they just handle HTTP concerns
(parsing the request, returning the response) and delegate everything else.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from database import get_db
from models import User, DraftStatus
from schemas import (
    DraftResponse, GenerateDraftRequest,
    ApproveDraftRequest, RejectDraftRequest,
)
from services import draft_service

router = APIRouter(prefix="/drafts", tags=["Drafts"])


@router.post(
    "",
    response_model=DraftResponse,
    status_code=201,
    summary="Generate an AI draft for an email",
)
def generate_draft(
    req: GenerateDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The main event — fetches an email from Gmail and generates a reply draft using AI.

    What happens behind the scenes:
    1. Fetches the source email from Gmail using the message_id you provide
    2. If tone is "auto", also fetches your recent sent emails to infer your writing style
    3. Sends everything to Claude with a carefully constructed prompt
    4. Saves the generated draft to the database with status "pending"
    5. Returns the draft for you to review

    Request body:
    - message_id: the Gmail message ID of the email you want to reply to
      (get this from GET /emails)
    - tone: how you want the reply to sound
      - "auto" — Claude reads your past sent emails and matches your natural style
      - "formal" — professional and structured, avoids contractions
      - "concise" — short and direct, no filler
      - "friendly" — warm and conversational

    The draft starts in "pending" status and won't be sent until you
    explicitly approve it via POST /drafts/{id}/approve.
    """
    return draft_service.create_draft(db, current_user, req)


@router.get(
    "",
    response_model=list[DraftResponse],
    summary="List your drafts",
)
def list_drafts(
    status: DraftStatus = Query(
        None,
        description="Filter by status: pending, approved, edited, rejected, sent"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns all your drafts, newest first.

    Use the status filter to focus on what matters:
    - ?status=pending   → drafts waiting for your review
    - ?status=sent      → everything that's already been sent
    - ?status=rejected  → drafts you decided not to send
    - (no filter)       → everything

    You can only see your own drafts — each user's drafts are completely
    isolated from other users.
    """
    return draft_service.list_drafts(db, current_user, status)


@router.get(
    "/{draft_id}",
    response_model=DraftResponse,
    summary="Get a single draft",
)
def get_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch the full details of a specific draft by its ID.

    Returns everything — the original email metadata, the AI-generated body,
    any edits you made, the current status, send attempts, and timestamps.
    Returns 404 if the draft doesn't exist or belongs to a different user.
    """
    return draft_service.get_draft(db, current_user, draft_id)


@router.post(
    "/{draft_id}/approve",
    response_model=DraftResponse,
    summary="Approve a draft (optionally with edits)",
)
def approve_draft(
    draft_id: str,
    req: ApproveDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Approve a pending draft so it can be sent.

    Two ways to use this:

    Approve as-is (you're happy with what Claude wrote):
        POST /drafts/{id}/approve
        {}

    Approve with your own edits (you want to tweak it before sending):
        POST /drafts/{id}/approve
        { "edited_body": "Hi Sarah,\\n\\nThanks for reaching out!..." }

    The status will be set to "approved" if you approve as-is,
    or "edited" if you provide an edited body. Either way, the draft
    is now eligible to be sent via POST /drafts/{id}/send.

    Only pending drafts can be approved. Trying to approve an already
    sent or rejected draft will return a 400 error.
    """
    return draft_service.approve_draft(db, current_user, draft_id, req)


@router.post(
    "/{draft_id}/reject",
    response_model=DraftResponse,
    summary="Reject a draft",
)
def reject_draft(
    draft_id: str,
    req: RejectDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reject a draft you don't want to send.

    The draft status becomes "rejected" and it can no longer be sent.
    You can optionally include a reason — this gets stored in the activity
    log which is useful for tracking patterns (e.g. the AI consistently
    gets the tone wrong for a certain type of email).

    If you want a different draft for the same email, just call
    POST /drafts again with the same message_id to generate a new one.
    """
    return draft_service.reject_draft(db, current_user, draft_id, req.reason)


@router.post(
    "/{draft_id}/send",
    response_model=DraftResponse,
    summary="Send an approved draft via Gmail",
)
def send_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send an approved or edited draft as a real Gmail reply.

    This is the final step. The system will:
    1. Send the draft via the Gmail API as a proper threaded reply
    2. Retry automatically up to 3 times if there's a transient error
    3. If all retries fail, send you a notification email and save the error
    4. On success, set the status to "sent" and record the Gmail message ID

    Only drafts in "approved" or "edited" status can be sent.
    Trying to send a "pending" draft returns a 400 — you must approve it first.

    Once sent, the draft cannot be unsent (that's Gmail's limitation, not ours).
    """
    return draft_service.send_draft(db, current_user, draft_id)