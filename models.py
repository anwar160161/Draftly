"""
Draftly - Database Models

This module defines all SQLAlchemy ORM models used by the application.

Models:
    - User
    - UserPreference
    - Draft
    - ActivityLog

Enums:
    - DraftStatus
    - TonePreference

These models represent the application's core business entities
and their relationships.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database import Base


# ============================================================================
# Enumerations
# ============================================================================

class DraftStatus(str, enum.Enum):
    """
    Represents the lifecycle state of an AI-generated draft.
    """

    PENDING = "pending"
    """Draft generated and awaiting user action."""

    APPROVED = "approved"
    """Draft approved by the user."""

    EDITED = "edited"
    """Draft modified by the user before approval."""

    REJECTED = "rejected"
    """Draft rejected by the user."""

    SENT = "sent"
    """Draft successfully sent via Gmail."""


class TonePreference(str, enum.Enum):
    """
    Supported email writing styles.
    """

    FORMAL = "formal"
    """Professional and structured communication."""

    CONCISE = "concise"
    """Short and direct communication."""

    FRIENDLY = "friendly"
    """Warm and conversational communication."""

    AUTO = "auto"
    """Automatically infer tone from user's sent emails."""


# ============================================================================
# User Model
# ============================================================================

class User(Base):
    """
    Represents an authenticated user.

    Stores Google account information, encrypted OAuth credentials,
    and relationships to user preferences, drafts, and activity logs.
    """

    __tablename__ = "users"

    # Google OAuth subject identifier
    id = Column(String, primary_key=True)

    # User profile information
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    picture = Column(String, nullable=True)

    # OAuth tokens encrypted using Fernet
    encrypted_access_token = Column(Text, nullable=True)
    encrypted_refresh_token = Column(Text, nullable=True)

    # Token expiration timestamp
    token_expiry = Column(DateTime, nullable=True)

    # Audit fields
    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    preferences = relationship(
        "UserPreference",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    drafts = relationship(
        "Draft",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    logs = relationship(
        "ActivityLog",
        back_populates="user",
        cascade="all, delete-orphan",
    )


# ============================================================================
# User Preferences Model
# ============================================================================

class UserPreference(Base):
    """
    Stores personalized email drafting preferences for a user.

    Each user can have only one preference record.
    """

    __tablename__ = "user_preferences"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
    )

    tone = Column(
        Enum(TonePreference),
        default=TonePreference.AUTO,
    )

    signature = Column(
        Text,
        nullable=True,
    )
    """Signature automatically appended to generated drafts."""

    custom_instructions = Column(
        Text,
        nullable=True,
    )
    """Additional instructions used during draft generation."""

    user = relationship(
        "User",
        back_populates="preferences",
    )


# ============================================================================
# Draft Model
# ============================================================================

class Draft(Base):
    """
    Represents an AI-generated email draft.

    Stores source email metadata, generated content,
    user decisions, and Gmail delivery information.
    """

    __tablename__ = "drafts"

    # Internal UUID
    id = Column(String, primary_key=True)

    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
    )

    # ------------------------------------------------------------------------
    # Source Email Metadata
    # ------------------------------------------------------------------------

    source_message_id = Column(
        String,
        nullable=False,
    )
    """Original Gmail message ID."""

    source_thread_id = Column(
        String,
        nullable=False,
    )
    """Original Gmail conversation thread ID."""

    source_subject = Column(
        String,
        nullable=True,
    )

    source_from = Column(
        String,
        nullable=True,
    )

    source_snippet = Column(
        Text,
        nullable=True,
    )
    """Short preview of the source email."""

    # ------------------------------------------------------------------------
    # AI Generated Draft
    # ------------------------------------------------------------------------

    draft_body = Column(
        Text,
        nullable=False,
    )

    tone_used = Column(
        Enum(TonePreference),
        nullable=True,
    )

    # ------------------------------------------------------------------------
    # User Decision Tracking
    # ------------------------------------------------------------------------

    status = Column(
        Enum(DraftStatus),
        default=DraftStatus.PENDING,
    )

    edited_body = Column(
        Text,
        nullable=True,
    )
    """Contains user-modified content if edited."""

    # ------------------------------------------------------------------------
    # Gmail Delivery Information
    # ------------------------------------------------------------------------

    gmail_message_id = Column(
        String,
        nullable=True,
    )

    send_attempts = Column(
        Integer,
        default=0,
    )

    last_error = Column(
        Text,
        nullable=True,
    )

    # ------------------------------------------------------------------------
    # Audit Fields
    # ------------------------------------------------------------------------

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sent_at = Column(
        DateTime,
        nullable=True,
    )

    # Relationships
    user = relationship(
        "User",
        back_populates="drafts",
    )

    logs = relationship(
        "ActivityLog",
        back_populates="draft",
        cascade="all, delete-orphan",
    )


# ============================================================================
# Activity Log Model
# ============================================================================

class ActivityLog(Base):
    """
    Stores user and draft-related events for auditing,
    monitoring, and debugging purposes.

    Examples:
        - draft_created
        - draft_approved
        - draft_rejected
        - draft_sent
        - login_success
    """

    __tablename__ = "activity_logs"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id = Column(
        String,
        ForeignKey("users.id"),
        nullable=False,
    )

    draft_id = Column(
        String,
        ForeignKey("drafts.id"),
        nullable=True,
    )

    event = Column(
        String,
        nullable=False,
    )

    detail = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    # Relationships
    user = relationship(
        "User",
        back_populates="logs",
    )

    draft = relationship(
        "Draft",
        back_populates="logs",
    )