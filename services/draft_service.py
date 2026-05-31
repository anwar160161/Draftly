"""
services/draft_service.py

The core of Draftly — orchestrates the entire draft lifecycle from
email fetch through AI generation to Gmail send.

This service is the glue between all the other pieces. It talks to
gmail_service to fetch emails, ai_service to generate drafts, and the
database to persist everything. The routers just call these functions;
all the actual decision-making happens here.

The lifecycle every draft goes through:

    create_draft()   → fetch email → generate with AI → save as "pending"
    approve_draft()  → mark as "approved" or "edited" (if user made changes)
    reject_draft()   → mark as "rejected" (won't be sent)
    send_draft()     → send via Gmail → mark as "sent" (with retry logic)

One important design decision: the system enforces the approve-before-send
rule at the service layer, not just the router layer. This means even if
someone calls send_draft() directly (e.g. in tests or scripts), it will
still refuse to send a pending draft. The rule is in the right place.

Every meaningful action is recorded in the ActivityLog so there's always
a full audit trail of what happened and when.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from requests.exceptions import HTTPError

from config import get_settings
from models import Draft, DraftStatus, TonePreference, ActivityLog, User
from schemas import GenerateDraftRequest, ApproveDraftRequest
from services import gmail_service, ai_service

settings = get_settings()


# ── Activity logging ──────────────────────────────────────────────────────────

def _log(
    db: Session,
    user_id: str,
    event: str,
    detail: str = None,
    draft_id: str = None,
) -> None:
    """
    Append an entry to the activity log.

    Every meaningful action in Draftly gets logged here — draft created,
    approved, sent, failed, etc. These logs are what GET /logs returns
    and are invaluable for debugging when something goes wrong.

    We commit immediately after each log entry rather than batching them,
    so the log is always up-to-date even if a later step in the same
    request fails.
    """
    entry = ActivityLog(
        user_id=user_id,
        draft_id=draft_id,
        event=event,
        detail=detail,
    )
    db.add(entry)
    db.commit()


# ── Create draft ──────────────────────────────────────────────────────────────

def create_draft(db: Session, user: User, req: GenerateDraftRequest) -> Draft:
    """
    Generate an AI draft for an incoming email and save it to the database.

    This is a four-step process:
    1. Fetch the source email from Gmail using the message_id
    2. If tone is AUTO, fetch recent sent emails to infer the user's style
    3. Call the AI service to generate the reply
    4. Save the draft to the database with status "pending"

    The sent email fetch in step 2 is best-effort — if it fails for any
    reason (expired token, empty sent folder, API error), we continue
    without it and fall back to a generic professional tone. We don't
    want style inference failures to block draft generation.

    Args:
        db:   Database session.
        user: The currently logged-in user.
        req:  Contains the message_id to reply to and optional tone override.

    Returns:
        The newly created Draft object with status "pending".
    """
    # Step 1: Fetch the email we're replying to.
    # This gives us the sender, subject, thread ID, and body we need.
    email = gmail_service.fetch_message(user, req.message_id)

    # Step 2: Fetch sent emails for style inference, but only if tone is AUTO.
    # There's no point fetching them for formal/concise/friendly since those
    # tones use fixed instructions rather than inferred style.
    sent_samples = []
    prefs = user.preferences
    effective_tone = req.tone or (prefs.tone if prefs else TonePreference.AUTO)

    if effective_tone == TonePreference.AUTO:
        try:
            sent_samples = gmail_service.fetch_sent_emails(user)
        except Exception:
            # Style inference failing is not a reason to fail the whole request.
            # We'll just generate without style context and use a default tone.
            pass

    # Step 3: Generate the draft using whichever AI service is configured.
    # We pass override_tone as None for AUTO so the AI service handles the
    # style inference logic itself rather than using an explicit instruction.
    draft_body, tone_used = ai_service.generate_draft(
        incoming_email=email,
        preferences=prefs,
        sent_samples=sent_samples,
        override_tone=req.tone if req.tone != TonePreference.AUTO else None,
    )

    # Step 4: Save the draft. We store enough of the source email metadata
    # that we can send the reply later without having to fetch it from Gmail again.
    draft = Draft(
        id=str(uuid.uuid4()),
        user_id=user.id,
        source_message_id=email["message_id"],
        source_thread_id=email["thread_id"],
        source_subject=email.get("subject"),
        source_from=email.get("sender"),
        source_snippet=email.get("snippet"),
        draft_body=draft_body,
        tone_used=tone_used,
        status=DraftStatus.PENDING,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    _log(db, user.id, "draft_created", f"message_id={req.message_id}", draft.id)
    return draft


# ── Approve draft ─────────────────────────────────────────────────────────────

def approve_draft(
    db: Session,
    user: User,
    draft_id: str,
    req: ApproveDraftRequest,
) -> Draft:
    """
    Approve a pending draft so it can be sent.

    Two outcomes depending on the request:
    - If edited_body is provided: saves the edited version and sets status
      to "edited". The edited body is what will be sent, not the original.
    - If no edited_body: sets status to "approved". The AI-generated body
      will be sent as-is.

    Both "approved" and "edited" are eligible for sending.
    Only "pending" drafts can be approved — anything else returns a 400.
    """
    draft = _get_draft_or_404(db, user, draft_id)
    _assert_status(draft, [DraftStatus.PENDING])

    if req.edited_body:
        # User made changes — save their version and mark as edited.
        # The original AI draft is preserved in draft_body for reference.
        draft.edited_body = req.edited_body
        draft.status = DraftStatus.EDITED
        _log(db, user.id, "draft_edited", None, draft.id)
    else:
        # User is happy with the AI draft — approve it as-is.
        draft.status = DraftStatus.APPROVED

    draft.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)

    _log(db, user.id, "draft_approved", None, draft.id)
    return draft


# ── Reject draft ──────────────────────────────────────────────────────────────

def reject_draft(
    db: Session,
    user: User,
    draft_id: str,
    reason: str = None,
) -> Draft:
    """
    Reject a draft the user doesn't want to send.

    Sets status to "rejected" — the draft can no longer be approved or sent.
    The reason is optional but useful for the activity log; it can help
    identify patterns in why drafts are being rejected (wrong tone, wrong
    content, misunderstood the email, etc.).

    If the user wants a different draft for the same email, they can call
    create_draft() again with the same message_id.
    """
    draft = _get_draft_or_404(db, user, draft_id)
    _assert_status(draft, [DraftStatus.PENDING])

    draft.status = DraftStatus.REJECTED
    draft.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)

    _log(db, user.id, "draft_rejected", reason, draft.id)
    return draft


# ── Send draft ────────────────────────────────────────────────────────────────

def _notify_user_of_failure(user: User, draft: Draft, error: str) -> None:
    """
    Send an alert email to the user when all send retries have been exhausted.

    This is a last resort — we've tried MAX_SEND_RETRIES times and the email
    still hasn't gone out. Rather than silently failing, we send the user an
    email via Gmail so they know something went wrong and can take action.

    This function itself is called inside a try/except and its failures are
    swallowed. If even this notification fails, we don't want it to mask the
    original error or cause the request to fail in a confusing way.
    """
    body = (
        f"Hi,\n\n"
        f"Draftly was unable to send your reply after {settings.MAX_SEND_RETRIES} attempts.\n\n"
        f"Draft ID: {draft.id}\n"
        f"Original email: {draft.source_subject}\n"
        f"Error: {error}\n\n"
        f"Please log in to review and retry sending.\n\n"
        f"— Draftly"
    )
    gmail_service.send_plain_email(
        user=user,
        to=user.email,
        subject="Draftly: Failed to send your reply",
        body=body,
    )


def send_draft(db: Session, user: User, draft_id: str) -> Draft:
    """
    Send an approved or edited draft as a real Gmail reply.

    This is the final step in the lifecycle. What happens:
    1. Validates the draft is in an approved/edited state
    2. Attempts to send via Gmail API with automatic retries
    3. On success: marks the draft as "sent", records the Gmail message ID
    4. On failure after all retries: saves the error, notifies the user by email

    Retry behaviour:
    - Retries up to MAX_SEND_RETRIES times (default: 3)
    - Waits RETRY_WAIT_SECONDS between each attempt (default: 2)
    - Only retries on HTTPError (transient API failures)
    - Other exceptions (auth errors, invalid data) fail immediately

    The send_attempts counter is incremented before the first attempt,
    so a value of 1 in the database means "tried once". If it equals
    MAX_SEND_RETRIES and status is not "sent", all retries were exhausted.

    Args:
        db:       Database session.
        user:     The currently logged-in user.
        draft_id: ID of the draft to send.

    Returns:
        The updated Draft object with status "sent" and gmail_message_id set.

    Raises:
        HTTPException(400): If the draft is not in an approved/edited state.
        HTTPException(404): If the draft doesn't exist or belongs to another user.
        Exception: Re-raises the last send error if all retries fail.
    """
    draft = _get_draft_or_404(db, user, draft_id)
    _assert_status(draft, [DraftStatus.APPROVED, DraftStatus.EDITED])

    # Use the user's edited version if they made changes, otherwise use
    # the original AI-generated body.
    body_to_send = draft.edited_body or draft.draft_body

    @retry(
        stop=stop_after_attempt(settings.MAX_SEND_RETRIES),
        wait=wait_fixed(settings.RETRY_WAIT_SECONDS),
        retry=retry_if_exception_type(HTTPError),
        reraise=True,
    )
    def _send():
        """Inner function so tenacity's retry decorator wraps just the API call."""
        return gmail_service.send_reply(
            user=user,
            thread_id=draft.source_thread_id,
            to=draft.source_from or "",
            subject=draft.source_subject or "",
            body=body_to_send,
            in_reply_to=draft.source_message_id,
        )

    try:
        draft.send_attempts += 1
        gmail_message_id = _send()

        # Send succeeded — update the draft with the Gmail message ID and timestamp.
        draft.status = DraftStatus.SENT
        draft.gmail_message_id = gmail_message_id
        draft.sent_at = datetime.utcnow()
        draft.last_error = None  # clear any error from a previous failed attempt
        _log(db, user.id, "draft_sent", f"gmail_id={gmail_message_id}", draft.id)

    except Exception as exc:
        # All retries failed — save the error and notify the user.
        draft.last_error = str(exc)
        _log(db, user.id, "send_failed", str(exc), draft.id)
        db.commit()

        # Only notify if we've hit the retry limit — not on intermediate failures
        # (tenacity handles those internally without surfacing them here).
        if draft.send_attempts >= settings.MAX_SEND_RETRIES:
            try:
                _notify_user_of_failure(user, draft, str(exc))
                _log(db, user.id, "failure_notification_sent", None, draft.id)
            except Exception:
                # Notification failing is unfortunate but not a reason to
                # change the error we raise back to the caller.
                pass

        raise

    draft.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)
    return draft


# ── List / get ────────────────────────────────────────────────────────────────

def list_drafts(
    db: Session,
    user: User,
    status: DraftStatus = None,
) -> list[Draft]:
    """
    Return all drafts for the current user, newest first.

    Optionally filter by status — useful for showing only pending drafts
    that need review, or only sent drafts for a history view.
    Users can only see their own drafts.
    """
    q = db.query(Draft).filter(Draft.user_id == user.id)
    if status:
        q = q.filter(Draft.status == status)
    return q.order_by(Draft.created_at.desc()).all()


def get_draft(db: Session, user: User, draft_id: str) -> Draft:
    """
    Return a single draft by ID.
    Raises 404 if the draft doesn't exist or belongs to a different user.
    """
    return _get_draft_or_404(db, user, draft_id)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_draft_or_404(db: Session, user: User, draft_id: str) -> Draft:
    """
    Fetch a draft by ID, scoped to the current user.

    The user scoping is important — it means users can't access each other's
    drafts by guessing IDs, even though UUIDs are hard to guess anyway.
    Returns 404 rather than 403 on ownership mismatch to avoid leaking
    information about whether a draft ID exists at all.
    """
    from fastapi import HTTPException
    draft = (
        db.query(Draft)
        .filter(Draft.id == draft_id, Draft.user_id == user.id)
        .first()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


def _assert_status(draft: Draft, allowed: list[DraftStatus]) -> None:
    """
    Enforce that a draft is in one of the expected states before proceeding.

    This is the mechanism that makes the lifecycle enforced at the service
    layer. Every state-changing function calls this first, so invalid
    transitions (like sending a pending draft) are caught with a clear
    error message rather than silently producing wrong behaviour.
    """
    from fastapi import HTTPException
    if draft.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Draft is currently '{draft.status}' — "
                f"expected one of: {[s.value for s in allowed]}."
            ),
        )