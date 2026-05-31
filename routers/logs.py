"""
routers/logs.py

A read-only audit trail of everything that happens in Draftly.

Every meaningful action in the system gets logged — draft created, approved,
edited, rejected, sent, failed. This endpoint exposes those logs so you can
see exactly what happened and when.

This is useful in a few different situations:
- Debugging: something went wrong, you want to trace the sequence of events
- Monitoring: checking how many drafts are being sent vs rejected
- Demo/presentation: showing a clear before-and-after of the full lifecycle

Logs are append-only and scoped to the current user — you can only see
your own activity.

Events you'll see in the logs:
    draft_created           → a new AI draft was generated
    draft_approved          → user approved a draft as-is
    draft_edited            → user approved with their own edits
    draft_rejected          → user rejected a draft
    draft_sent              → draft was successfully sent via Gmail
    send_failed             → Gmail send attempt failed (includes error detail)
    failure_notification_sent → user was emailed about a persistent send failure
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from database import get_db
from models import User, ActivityLog
from schemas import ActivityLogEntry

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get(
    "",
    response_model=list[ActivityLogEntry],
    summary="Get your activity logs",
)
def get_logs(
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Number of log entries to return (default 50, max 200)",
    ),
    skip: int = Query(
        0,
        ge=0,
        description="Number of entries to skip — use this for pagination",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns your activity log entries, newest first.

    Each log entry tells you:
    - event: what happened (e.g. "draft_sent", "send_failed")
    - detail: extra context where relevant (e.g. the Gmail message ID on send,
      or the error message on failure)
    - draft_id: which draft the event relates to (if applicable)
    - created_at: exactly when it happened

    Pagination example — fetch page 2 of 50 results:
        GET /logs?limit=50&skip=50

    You can only see your own logs. Each user's activity is completely
    isolated from other users.
    """
    logs = (
        db.query(ActivityLog)
        .filter(ActivityLog.user_id == current_user.id)
        .order_by(ActivityLog.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return logs