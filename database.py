"""
Draftly - Database Configuration

This module is responsible for configuring SQLAlchemy and managing
database connections for the application.

By default, SQLite is used for local development, but the
DATABASE_URL can be changed to PostgreSQL, MySQL, or any
other SQLAlchemy-supported database without modifying
application code.

It provides:
    - SQLAlchemy Engine
    - Session Factory
    - Declarative Base Class
    - FastAPI Database Dependency
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    sessionmaker,
)

from config import get_settings

# Load application configuration
settings = get_settings()


# ============================================================================
# Database Engine Configuration
# ============================================================================

# SQLite requires this flag to allow usage across multiple threads.
# Other databases (PostgreSQL, MySQL, etc.) do not require it.
connect_args = (
    {"check_same_thread": False}
    if "sqlite" in settings.DATABASE_URL
    else {}
)

# Create SQLAlchemy engine
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=settings.DEBUG,
)
"""
Database engine used by SQLAlchemy.

Parameters:
    DATABASE_URL:
        Connection string loaded from application settings.

    connect_args:
        SQLite-specific configuration.

    echo:
        Enables SQL query logging when DEBUG=True.
"""


# ============================================================================
# Session Factory
# ============================================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)
"""
Factory for creating database sessions.

Configuration:
    autocommit=False
        Transactions must be committed explicitly.

    autoflush=False
        Changes are not automatically flushed to the database.

    bind=engine
        Associates sessions with the configured engine.
"""


# ============================================================================
# Base Model Class
# ============================================================================

class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    Every database model should inherit from this class.

    Example:
        class User(Base):
            __tablename__ = "users"
    """

    pass


# ============================================================================
# FastAPI Dependency
# ============================================================================

def get_db():
    """
    Provide a database session for FastAPI endpoints.

    This dependency creates a new database session for each request
    and guarantees proper cleanup after the request completes,
    even if an exception occurs.

    Usage:
        @router.get("/users")
        def get_users(db: Session = Depends(get_db)):
            ...

    Yields:
        Session: Active SQLAlchemy database session.
    """
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()