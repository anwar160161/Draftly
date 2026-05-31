"""
Draftly - Application Configuration

This module centralizes all application settings and loads them
from environment variables or a .env file.

The Settings class uses Pydantic Settings for automatic validation
and type conversion of configuration values.

Usage:
    from config import get_settings

    settings = get_settings()
    print(settings.DATABASE_URL)
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration settings.

    Values are loaded from:
        1. Environment variables
        2. .env file
        3. Default values (fallback)

    Pydantic automatically validates and converts values
    to the appropriate Python types.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # ========================================================================
    # Application Settings
    # ========================================================================

    APP_NAME: str = "Draftly"
    """Application name displayed in documentation and UI."""

    DEBUG: bool = False
    """Enable verbose debugging and development features."""

    SECRET_KEY: str = "change-me-in-production"
    """Secret key used for JWT token signing and security operations."""

    # ========================================================================
    # Database Configuration
    # ========================================================================

    DATABASE_URL: str = "sqlite:///./draftly.db"
    """
    Database connection string.

    Default:
        SQLite database stored locally.

    Production Example:
        postgresql://user:password@localhost/draftly
    """

    # ========================================================================
    # Google OAuth & Gmail API
    # ========================================================================

    GOOGLE_CLIENT_ID: str = ""
    """Google OAuth client ID."""

    GOOGLE_CLIENT_SECRET: str = ""
    """Google OAuth client secret."""

    GOOGLE_REDIRECT_URI: str = (
        "http://localhost:8000/auth/callback"
    )
    """OAuth callback URL registered in Google Cloud Console."""

    GMAIL_SCOPES: list[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "openid",
        "email",
        "profile",
    ]
    """
    OAuth scopes requested during Google authentication.

    Includes:
        - Reading emails
        - Sending emails
        - Draft creation
        - User profile information
    """

    # ========================================================================
    # AI Model Configuration
    # ========================================================================

    ANTHROPIC_API_KEY: str = ""
    """API key used for Anthropic Claude models."""

    AI_MODEL: str = "claude-haiku-4-5"
    """Default AI model used for email generation."""

    # ------------------------------------------------------------------------
    # Optional Gemini Support
    # ------------------------------------------------------------------------

    GEMINI_API_KEY: str = ""
    """API key for Google's Gemini models."""

    # Example:
    # AI_MODEL: str = "gemini-1.5-flash"
    # AI_MODEL: str = "gemini-1.5-pro"

    # ------------------------------------------------------------------------
    # Optional OpenAI Support
    # ------------------------------------------------------------------------

    OPENAI_API_KEY: str = ""
    """API key for OpenAI models."""

    # Example:
    # AI_MODEL: str = "gpt-4o-mini"
    # AI_MODEL: str = "gpt-4o"

    # ========================================================================
    # Security & Encryption
    # ========================================================================

    TOKEN_ENCRYPTION_KEY: str = ""
    """
    Fernet encryption key used to encrypt OAuth access tokens.

    Must be a valid URL-safe Base64 encoded key.
    """

    # ========================================================================
    # Email Retry Configuration
    # ========================================================================

    MAX_SEND_RETRIES: int = 3
    """Maximum retry attempts when email sending fails."""

    RETRY_WAIT_SECONDS: int = 2
    """Delay between retry attempts in seconds."""

    # ========================================================================
    # Email Fetch Configuration
    # ========================================================================

    DEFAULT_EMAIL_FETCH_LIMIT: int = 10
    """Default number of inbox emails fetched per request."""

    DEFAULT_SENT_FETCH_LIMIT: int = 2
    """
    Number of recently sent emails fetched for
    writing style analysis and personalization.
    """


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Using lru_cache ensures the configuration is loaded
    only once during application startup and reused
    throughout the application's lifecycle.

    Returns:
        Settings: Application configuration object.
    """
    return Settings()