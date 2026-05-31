"""
Draftly - Pydantic Schemas

This module defines all request and response DTOs (Data Transfer Objects)
used by the FastAPI application.

Schemas are grouped into:
    - Authentication
    - Email Operations
    - User Preferences
    - Draft Management
    - Activity Logs
    - Generic Responses

These schemas provide:
    - Request validation
    - Response serialization
    - OpenAPI documentation generation
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from models import DraftStatus, TonePreference


# ============================================================================
# Authentication Schemas
# ============================================================================

class AuthURLResponse(BaseModel):
    """
    Response returned when initiating Google OAuth.

    Attributes:
        authorization_url: URL where the user should authenticate.
        state: OAuth state token for CSRF protection.
    """

    authorization_url: str
    state: str


class TokenResponse(BaseModel):
    """
    JWT authentication response.

    Attributes:
        access_token: Generated JWT token.
        token_type: Authentication scheme.
    """

    access_token: str
    token_type: str = "bearer"


class UserProfile(BaseModel):
    """
    Public user profile information.

    Attributes:
        id: Google user identifier.
        email: User email address.
        name: Display name.
        picture: Profile image URL.
    """

    id: str
    email: str
    name: Optional[str]
    picture: Optional[str]

    model_config = {"from_attributes": True}


# ============================================================================
# Email Schemas
# ============================================================================

class EmailSummary(BaseModel):
    """
    Lightweight email representation used in inbox listings.

    Attributes:
        message_id: Gmail message identifier.
        thread_id: Gmail conversation thread identifier.
        subject: Email subject.
        sender: Email sender.
        snippet: Preview text.
        date: Email timestamp.
    """

    message_id: str
    thread_id: str
    subject: Optional[str]
    sender: Optional[str]
    snippet: str
    date: Optional[str]


class EmailDetail(EmailSummary):
    """
    Detailed email representation.

    Extends EmailSummary with the complete email body.
    """

    body: str


# ============================================================================
# User Preference Schemas
# ============================================================================

class PreferenceUpdate(BaseModel):
    """
    Request schema used to update user preferences.

    All fields are optional to allow partial updates.
    """

    tone: Optional[TonePreference] = None
    signature: Optional[str] = None
    custom_instructions: Optional[str] = None


class PreferenceResponse(BaseModel):
    """
    Response schema representing user preferences.

    Attributes:
        tone: Preferred writing tone.
        signature: User email signature.
        custom_instructions: Additional drafting instructions.
    """

    tone: TonePreference
    signature: Optional[str]
    custom_instructions: Optional[str]

    model_config = {"from_attributes": True}


# ============================================================================
# Draft Schemas
# ============================================================================

class GenerateDraftRequest(BaseModel):
    """
    Request used to generate an AI email draft.

    Attributes:
        message_id: Gmail message ID being replied to.
        tone: Desired writing tone.
    """

    message_id: str = Field(
        ...,
        description="Gmail message ID to reply to",
    )

    tone: Optional[TonePreference] = TonePreference.AUTO


class DraftResponse(BaseModel):
    """
    Complete draft response object.

    Represents a generated email draft along with
    metadata, status, and delivery information.
    """

    id: str

    source_message_id: str
    source_thread_id: str

    source_subject: Optional[str]
    source_from: Optional[str]
    source_snippet: Optional[str]

    draft_body: str
    tone_used: Optional[TonePreference]

    status: DraftStatus

    edited_body: Optional[str]

    gmail_message_id: Optional[str]

    send_attempts: int
    last_error: Optional[str]

    created_at: datetime
    updated_at: datetime
    sent_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApproveDraftRequest(BaseModel):
    """
    Request used to approve a draft.

    Optionally allows the user to submit an edited
    version before approval.
    """

    edited_body: Optional[str] = Field(
        None,
        description=(
            "Provide an edited body to approve with changes; "
            "omit to approve as-is."
        ),
    )


class RejectDraftRequest(BaseModel):
    """
    Request used to reject a draft.

    Attributes:
        reason: Optional explanation for rejection.
    """

    reason: Optional[str] = None


# ============================================================================
# Activity Log Schemas
# ============================================================================

class ActivityLogEntry(BaseModel):
    """
    Activity log response.

    Represents a tracked user or system event.

    Examples:
        - draft_created
        - draft_approved
        - draft_sent
        - login_success
    """

    id: int
    event: str
    detail: Optional[str]
    draft_id: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# Generic Response Schemas
# ============================================================================

class MessageResponse(BaseModel):
    """
    Generic success response.

    Used for simple API responses that only
    need to return a message.

    Example:
        {
            "message": "Draft approved successfully"
        }
    """

    message: str