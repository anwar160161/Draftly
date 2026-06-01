# Draftly – Gmail AI Reply Agent

A backend system that connects to your Gmail, reads incoming emails, and generates smart reply drafts using Claude AI. You stay in control — every draft goes through a review step before anything gets sent.

Built with FastAPI, SQLAlchemy, Google Gmail API, and Anthropic Claude.

---

## Table of Contents

1. [Why I Built It This Way](#why-i-built-it-this-way)
2. [How It Works](#how-it-works)
3. [System Architecture](#system-architecture)
4. [Project Structure](#project-structure)
5. [Database Design](#database-design)
6. [Key Design Decisions](#key-design-decisions)
7. [Setup and Installation](#setup-and-installation)
8. [Running with Docker](#running-with-docker)
9. [Environment Variables](#environment-variables)
10. [API Reference](#api-reference)
11. [Testing the Full Flow](#testing-the-full-flow)
12. [Security Considerations](#security-considerations)
13. [What Could Be Better](#what-could-be-better-future-work)

---

## Why I Built It This Way

The core idea was simple: professionals waste a lot of time writing the same kinds of replies — meeting confirmations, acknowledgements, follow-ups. Automating the *drafting* part while keeping the human in the loop for *sending* felt like the right balance.

I made a deliberate choice early on: **the system never sends anything without explicit approval**. This isn't just a safety feature — it's the whole design philosophy. The AI suggests, the human decides.

---

## How It Works

Here's the full flow from start to finish:

1. User logs in with their Google account via OAuth2
2. The system fetches unread emails from their Gmail inbox
3. User picks an email and requests a draft reply
4. The system fetches the user's last few sent emails to understand their writing style
5. Claude AI generates a draft that matches their tone, phrasing, and style
6. Draft is saved to the database with status `pending`
7. User reviews the draft — they can approve it, edit it, or reject it
8. Once approved, the system sends it via Gmail with proper thread headers
9. Everything is logged — every draft created, approved, rejected, or sent

---

## System Architecture

```
Client (Swagger / Frontend)
           │
           ▼
    ┌─────────────┐
    │   FastAPI   │  ← All functionality exposed as RESTful APIs
    └──────┬──────┘
           │
    ┌──────▼──────────────────────────┐
    │         Router Layer            │
    │  /auth  /emails  /drafts        │
    │  /preferences  /logs            │
    └──────┬──────────────────────────┘
           │
    ┌──────▼──────────────────────────┐
    │         Service Layer           │
    │                                 │
    │  GmailService                   │  ← Talks to Gmail API
    │  AIService                      │  ← Talks to Claude API
    │  DraftService                   │  ← Orchestrates everything
    └──────┬──────────────────────────┘
           │
    ┌──────▼──────────────────────────┐
    │      Database (SQLAlchemy)      │
    │  User, Draft, UserPreference    │
    │  ActivityLog                    │
    └─────────────────────────────────┘
           │                │
           ▼                ▼
      Gmail API        Anthropic API
      (OAuth2)         (Claude Haiku)
```

The architecture follows a clean separation:
- **Routers** only handle HTTP — parsing requests, returning responses
- **Services** contain all the business logic
- **Models** are pure database definitions, no logic in them
- **Schemas** are pure data shapes for API input/output

---

## Project Structure

```
draftly/
├── main.py                  # App entry point, router registration, DB setup
├── config.py                # All settings via pydantic-settings (reads .env)
├── database.py              # SQLAlchemy engine and session management
├── models.py                # Database models: User, Draft, UserPreference, ActivityLog
├── schemas.py               # Pydantic DTOs for all API inputs and outputs
├── Dockerfile               # Docker container definition
├── docker-compose.yml       # Docker Compose configuration
│
├── auth/
│   ├── oauth.py             # Google OAuth2 flow, token exchange, Fernet encryption
│   └── dependencies.py      # JWT creation and get_current_user dependency
│
├── services/
│   ├── gmail_service.py     # All Gmail API calls (fetch inbox, fetch sent, send)
│   ├── ai_service.py        # Claude AI draft generation with style inference
│   └── draft_service.py     # Full draft lifecycle with retry logic
│
├── routers/
│   ├── auth.py              # Login, callback, profile, logout
│   ├── emails.py            # Inbox listing and single email fetch
│   ├── drafts.py            # Generate, list, approve, reject, send
│   ├── preferences.py       # User tone and signature settings
│   └── logs.py              # Activity log endpoint
│
├── requirements.txt
├── .env.example
└── README.md
```

---

## Database Design

Four tables:

**users** — Stores Google profile info and encrypted OAuth tokens. Tokens are encrypted with Fernet (AES-128) before hitting the database. Never stored in plaintext.

**user_preferences** — One row per user. Stores their preferred tone (formal/concise/friendly/auto), email signature, and any custom AI instructions.

**drafts** — The core table. Stores the source email metadata, the AI-generated body, the user's edited version (if any), current status, send attempts, and timestamps. Every state change is captured here.

**activity_logs** — An append-only audit trail. Every meaningful event (draft created, approved, sent, failed) gets a log entry. Useful for debugging and monitoring.

Draft status flow:
```
pending → approved → sent
pending → edited   → sent
pending → rejected
```

---

## Key Design Decisions

### 1. Draft-first, always
The system enforces a two-step: approve first, then send. Calling `/drafts/{id}/send` on a pending draft returns a 400 error. This isn't just validation — it's the contract the whole system is built around. The user is always in the loop.

### 2. Style inference with AUTO tone
When tone is set to `auto`, the system fetches the user's last few sent emails and includes them in the Claude prompt. Claude reads those samples and mirrors the user's natural writing style — their greeting style, sentence length, level of formality, how they sign off. No explicit configuration needed.

### 3. Encrypted token storage
Gmail OAuth tokens are sensitive. Before saving them to the database, they go through Fernet symmetric encryption. In development, the key is derived from SECRET_KEY. In production, set a proper TOKEN_ENCRYPTION_KEY (a real 32-byte Fernet key).

### 4. JWT sessions
After OAuth login, the server issues a short-lived JWT (24 hours). All subsequent API calls carry this token in the Authorization header. The backend stays stateless — no server-side sessions, no cookies.

### 5. Retry logic on send
Sending emails can fail transiently — rate limits, network blips, temporary Gmail errors. The send function uses `tenacity` to retry up to 3 times with a 2-second wait between attempts. The attempt count and last error are both saved to the draft record so you can audit failures.

### 6. Failure notifications
If all retries are exhausted, the system sends an alert email to the user via Gmail so they know something went wrong and can take action manually.

### 7. Proper email threading (Gmail + Outlook)
Replies use `In-Reply-To`, `References`, `Thread-Topic`, and `Thread-Index` headers to ensure replies appear in the correct thread on both Gmail and Outlook clients.

### 8. One database URL to rule them all
The project uses SQLite by default. Switching to PostgreSQL for production is a single line change in `.env`. No code changes needed.

---

## Setup and Installation

### What you need
- Python 3.11 or higher (or Docker)
- A Google Cloud project with Gmail API enabled
- An Anthropic API key

### Step 1 — Google Cloud setup
1. Go to https://console.cloud.google.com
2. Create a project and enable the **Gmail API**
3. Go to Credentials → Create OAuth 2.0 Client ID (type: Web Application)
4. Add `http://localhost:8000/auth/callback` as an authorized redirect URI
5. Note down your Client ID and Client Secret
6. Go to OAuth consent screen → Test users → add your Gmail address

### Step 2 — Clone and install
```bash
git clone https://github.com/your-username/draftly.git
cd draftly
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3 — Configure
```bash
cp .env.example .env
# Edit .env and fill in the required values
```

### Step 4 — Run
```bash
uvicorn main:app --reload
```

Open http://localhost:8000/docs

---

## Running with Docker

### Option A — Docker Compose (recommended)

```bash
# 1. Clone the repo
git clone https://github.com/your-username/draftly.git
cd draftly

# 2. Set up environment
cp .env.example .env
# Edit .env and fill in your keys

# 3. Build and start
docker-compose up --build

# 4. Open Swagger UI
# http://localhost:8000/docs
```

To stop:
```bash
docker-compose down
```

### Option B — Docker directly

```bash
# Build the image
docker build -t draftly .

# Run the container
docker run -p 8000:8000 --env-file .env draftly
```

### Notes on Docker
- The SQLite database is mounted as a volume in `docker-compose.yml` so data persists between container restarts
- For production, replace SQLite with PostgreSQL by updating `DATABASE_URL` in `.env`
- The container runs on port 8000 by default

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | ✅ | — | JWT signing key — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | — | sqlite:///./draftly.db | Swap for PostgreSQL in production |
| `GOOGLE_CLIENT_ID` | ✅ | — | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | ✅ | — | From Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | — | http://localhost:8000/auth/callback | Must match Google Console exactly |
| `ANTHROPIC_API_KEY` | ✅ | — | From console.anthropic.com |
| `AI_MODEL` | — | claude-haiku-4-5 | Claude model to use |
| `TOKEN_ENCRYPTION_KEY` | — | Derived from SECRET_KEY | Fernet key for token encryption |
| `MAX_SEND_RETRIES` | — | 3 | Retry attempts on send failure |
| `RETRY_WAIT_SECONDS` | — | 2 | Seconds between retries |
| `DEFAULT_EMAIL_FETCH_LIMIT` | — | 10 | Max emails fetched from inbox |
| `DEFAULT_SENT_FETCH_LIMIT` | — | 5 | Sent emails used for style inference |

---

## API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /auth/login | Returns Google OAuth2 authorization URL |
| GET | /auth/callback?code=… | Exchanges code for tokens, returns JWT |
| GET | /auth/me | Returns current user profile |
| POST | /auth/logout | Clears stored Gmail tokens |

After `/auth/callback`, use the returned token in every request:
```
Authorization: Bearer <your-jwt-token>
```

---

### Emails

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /emails?limit=10 | List recent unread inbox emails |
| GET | /emails/{message_id} | Get full body of a specific email |

---

### Drafts

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /drafts | Generate an AI draft for a Gmail message |
| GET | /drafts | List all drafts (filter by ?status=pending etc.) |
| GET | /drafts/{id} | Get a single draft |
| POST | /drafts/{id}/approve | Approve as-is, or approve with edited body |
| POST | /drafts/{id}/reject | Reject the draft |
| POST | /drafts/{id}/send | Send an approved draft via Gmail |

**Generate a draft:**
```json
POST /drafts
{
  "message_id": "19e7cbafc7a52a17",
  "tone": "formal"
}
```

Tone options: `auto` | `formal` | `concise` | `friendly`

**Approve with edits:**
```json
POST /drafts/{id}/approve
{
  "edited_body": "Hi,\n\nThanks for reaching out. Happy to connect!\n\nBest regards"
}
```

**Approve as-is:**
```json
POST /drafts/{id}/approve
{}
```

Draft status progression:
```
pending → approved → sent
pending → edited   → sent
pending → rejected
```

---

### Preferences

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /preferences | Get your current preferences |
| PATCH | /preferences | Update tone, signature, or custom instructions |

```json
PATCH /preferences
{
  "tone": "friendly",
  "signature": "Best regards,\nAnwar",
  "custom_instructions": "Keep replies under 3 sentences when possible."
}
```

---

### Logs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /logs?limit=50&skip=0 | Get activity logs, newest first |

---

## Testing the Full Flow

1. Start server: `uvicorn main:app --reload` (or `docker-compose up`)
2. Open http://localhost:8000/docs
3. `GET /auth/login` → copy the URL → open in browser → sign in
4. Copy the JWT from the callback response
5. Click **Authorize** in Swagger → paste the token
6. `GET /emails` → copy a message_id
7. `POST /drafts` with that message_id and tone: "formal"
8. `POST /drafts/{id}/approve` with `{}`
9. `POST /drafts/{id}/send`
10. `GET /logs` → see the full audit trail

---

## Security Considerations

- `.env` is in `.gitignore` — never committed to version control
- Gmail OAuth tokens are Fernet-encrypted before database storage
- JWTs expire after 24 hours
- Only the authenticated user can access their own drafts and logs
- The system never sends without explicit user approval
- In production: use HTTPS, restrict CORS origins, use a proper Fernet key, use PostgreSQL

---

## What Could Be Better (Future Work)

- **Token auto-refresh** — Currently the Gmail access token expires after 1 hour and the user has to log in again. The refresh token is stored and could be used to silently renew access.
- **Gmail Push Notifications** — Instead of manually fetching emails, Gmail's Pub/Sub push can notify the backend when new mail arrives and auto-generate drafts.
- **Multiple AI providers** — The ai_service can be abstracted behind an interface to swap between Claude, Gemini, or OpenAI without changing any other code.
- **Rate limiting** — Add per-user request limits to stay within Gmail API quotas.
