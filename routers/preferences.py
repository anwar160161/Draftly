"""
routers/preferences.py

Lets users personalise how Draftly generates their email drafts.

There are three things you can configure:

    tone
        Controls the overall writing style Claude uses.
        - "auto"     → Claude reads your last few sent emails and mirrors
                        your natural style automatically. Best default.
        - "formal"   → Professional language, no contractions, structured.
        - "concise"  → Short and direct. Gets to the point fast.
        - "friendly" → Warm and conversational, but still professional.

    signature
        Text appended to the end of every generated draft.
        Set this to your usual sign-off so you never have to add it manually.
        Example: "Best regards,\\nAnwar"

    custom_instructions
        Freeform instructions passed directly to Claude on every draft.
        Use this for anything specific to how you communicate.
        Examples:
        - "Always mention my availability when responding to meeting requests."
        - "Keep replies under 3 sentences unless the topic requires more detail."
        - "Never use bullet points in emails."

These preferences are applied automatically every time a draft is generated —
you set them once and forget about them.

Both endpoints use PATCH semantics, meaning you only need to send the fields
you want to change. Omitted fields are left exactly as they are.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from database import get_db
from models import User, UserPreference
from schemas import PreferenceUpdate, PreferenceResponse

router = APIRouter(prefix="/preferences", tags=["Preferences"])


@router.get(
    "",
    response_model=PreferenceResponse,
    summary="Get your current preferences",
)
def get_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns your current draft generation preferences.

    If you haven't set any preferences yet, this returns the defaults:
    - tone: "auto" (Claude infers your style from sent emails)
    - signature: none
    - custom_instructions: none

    Preferences are created automatically on first login, so this endpoint
    will always return something — it never 404s.
    """
    prefs = current_user.preferences
    if not prefs:
        # Preferences should be created at signup, but we create them
        # here as a safety net in case something went wrong during registration.
        prefs = UserPreference(user_id=current_user.id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


@router.patch(
    "",
    response_model=PreferenceResponse,
    summary="Update your preferences",
)
def update_preferences(
    req: PreferenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update one or more of your draft generation preferences.

    This uses PATCH semantics — you only need to include the fields you
    want to change. Everything else stays exactly as it was.

    Examples:

    Just change your tone:
        { "tone": "concise" }

    Just add a signature:
        { "signature": "Best regards,\\nAnwar" }

    Update everything at once:
        {
            "tone": "friendly",
            "signature": "Cheers,\\nAnwar",
            "custom_instructions": "Keep replies short and avoid jargon."
        }

    Clear your signature (set it back to nothing):
        { "signature": "" }

    Changes take effect immediately — the next draft you generate will
    use the updated preferences.
    """
    prefs = current_user.preferences
    if not prefs:
        # Same safety net as the GET endpoint — shouldn't happen in normal
        # flow but we handle it gracefully rather than crashing.
        prefs = UserPreference(user_id=current_user.id)
        db.add(prefs)

    # Only update fields that were explicitly included in the request.
    # We check for None rather than truthiness so that empty string ("")
    # is a valid value — it lets users clear their signature or instructions.
    if req.tone is not None:
        prefs.tone = req.tone
    if req.signature is not None:
        prefs.signature = req.signature
    if req.custom_instructions is not None:
        prefs.custom_instructions = req.custom_instructions

    db.commit()
    db.refresh(prefs)
    return prefs