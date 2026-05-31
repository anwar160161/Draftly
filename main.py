"""
Draftly - FastAPI Application Entry Point

This module serves as the main entry point for the Draftly API.

Responsibilities:
    - Initialize FastAPI application
    - Configure middleware
    - Register API routers
    - Create database tables
    - Expose health check endpoint

Available Modules:
    - Authentication
    - Gmail Integration
    - Draft Management
    - User Preferences
    - Activity Logs

Run:
    uvicorn main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import Base, engine
from routers import (
    auth,
    drafts,
    emails,
    logs,
    preferences,
)

# Load application configuration
settings = get_settings()


# ============================================================================
# Database Initialization
# ============================================================================

# Create all database tables during application startup.
#
# NOTE:
# For production environments, consider using Alembic migrations
# instead of automatic table creation.
Base.metadata.create_all(bind=engine)


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Draftly - Gmail AI Reply Agent",
    description=(
        "An AI-powered email assistant that connects to Gmail, "
        "analyzes incoming emails, generates contextual reply drafts "
        "using AI, and sends approved responses on behalf of users."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
"""
Main FastAPI application instance.

Features:
    - Interactive Swagger UI (/docs)
    - ReDoc Documentation (/redoc)
    - Automatic OpenAPI schema generation
"""


# ============================================================================
# Middleware Configuration
# ============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
"""
Enable Cross-Origin Resource Sharing (CORS).

Current Configuration:
    Allows requests from any origin.

Production Recommendation:
    Replace '*' with a list of trusted frontend domains.
"""


# ============================================================================
# Router Registration
# ============================================================================

# Authentication and OAuth endpoints
app.include_router(auth.router)

# Gmail inbox and email retrieval endpoints
app.include_router(emails.router)

# AI draft generation and email sending endpoints
app.include_router(drafts.router)

# User preference management endpoints
app.include_router(preferences.router)

# Activity log and audit endpoints
app.include_router(logs.router)


# ============================================================================
# Health Check Endpoint
# ============================================================================

@app.get(
    "/",
    tags=["Health"],
    summary="Application Health Check",
)
def health_check():
    """
    Verify that the application is running.

    This endpoint can be used by:
        - Load balancers
        - Monitoring systems
        - Health probes
        - Developers

    Returns:
        dict: Basic application status information.
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": app.version,
    }