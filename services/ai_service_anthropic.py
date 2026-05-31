"""
services/ai_service.py

Responsible for generating email reply drafts using Claude (Anthropic).

This is where the "AI" in Draftly actually lives. Given an incoming email
and some context about the user, this service constructs a prompt and asks
Claude to write a reply that sounds like it came from the user — not from
a robot.

The most interesting part of this service is the AUTO tone feature. Instead
of asking the user to describe their writing style (nobody wants to do that),
we fetch their last few sent emails and let Claude figure it out. Claude reads
those samples and picks up on things like:
- How they greet people ("Hi", "Hello", "Hey", "Dear")
- How formal their language is
- Whether they use bullet points or paragraphs
- How they sign off ("Best", "Thanks", "Regards")
- Their average sentence and email length

The result is a draft that feels like something the user would actually write,
not a generic AI-sounding reply.

How the prompt is structured:
    System prompt — tells Claude who it is and how to behave
        + tone instructions (explicit or inferred from sent emails)
        + user's custom instructions (if any)
        + user's signature (if set)
    User message — the actual email Claude needs to reply to
"""

import anthropic

from config import get_settings
from models import TonePreference, UserPreference

settings = get_settings()

# Single shared client instance — no need to create a new one per request.
# The Anthropic client is thread-safe and designed to be reused.
_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# These instructions are injected into the system prompt when the user
# has selected a specific tone. They tell Claude exactly what "formal"
# or "concise" means in practice, rather than leaving it to interpretation.
TONE_INSTRUCTIONS = {
    TonePreference.FORMAL:   "Use formal, professional language. Avoid contractions and colloquialisms.",
    TonePreference.CONCISE:  "Be brief and to-the-point. Use short sentences. Avoid filler words.",
    TonePreference.FRIENDLY: "Use a warm, conversational tone. Light and approachable, but still professional.",
    TonePreference.AUTO:     "",  # AUTO tone is handled dynamically via style inference below
}


def _build_style_context(sent_samples: list[dict]) -> str:
    """
    Build a prompt fragment that teaches Claude the user's writing style.

    We take the user's recent sent emails and format them as examples for
    Claude to study. Claude is remarkably good at picking up on subtle style
    patterns from just a few samples — things like whether someone writes
    "Thanks for reaching out" vs "Thank you for contacting me" signal a lot
    about their communication style.

    We cap each email body at 500 characters to keep the prompt a reasonable
    size. The style signals we care about (greeting, tone, sign-off) are
    almost always in the first and last few lines anyway.

    Returns an empty string if no sent emails are available, which causes
    the caller to fall back to a generic professional tone.
    """
    if not sent_samples:
        return ""

    # Format each sent email as a labelled sample separated by dividers.
    # The dividers help Claude understand where one email ends and the next begins.
    samples_text = "\n\n---\n\n".join(
        f"Subject: {s.get('subject', '')}\n{s.get('body', '')[:500]}"
        for s in sent_samples
    )

    return (
        "Here are some recent emails the user has sent. "
        "Study their tone, phrasing, greeting/closing style, sentence length, and formality level. "
        "Mirror this style in your reply.\n\n"
        f"{samples_text}\n\n"
    )


def generate_draft(
    incoming_email: dict,
    preferences: UserPreference | None,
    sent_samples: list[dict] | None = None,
    override_tone: TonePreference | None = None,
) -> tuple[str, TonePreference]:
    """
    Generate a reply draft for an incoming email using Claude.

    Args:
        incoming_email: The email to reply to, as returned by gmail_service.
                        Must contain at least 'sender', 'subject', and either
                        'body' or 'snippet'.
        preferences:    The user's saved preferences (tone, signature,
                        custom instructions). Can be None if not yet set.
        sent_samples:   Recent emails the user has sent, used for style
                        inference when tone is AUTO. Optional.
        override_tone:  If provided, uses this tone regardless of what's
                        saved in preferences. Used when the user specifies
                        a tone per-request in the API.

    Returns:
        A tuple of (draft_body, tone_used) where:
        - draft_body is the raw text of the generated reply
        - tone_used is which TonePreference was actually applied
    """
    # Determine which tone to use, in order of priority:
    # 1. override_tone from the API request (most specific)
    # 2. tone from saved preferences
    # 3. AUTO as the fallback if nothing is set
    tone = override_tone or (preferences.tone if preferences else TonePreference.AUTO)

    # ── Build the system prompt ───────────────────────────────────────────────
    # The system prompt is Claude's instruction set — it defines the role,
    # the tone, and any user-specific rules that apply to every draft.
    # We build it in parts and join them so it's easy to add or remove sections.

    system_parts = [
        "You are Draftly, an expert email assistant. "
        "Your job is to write a high-quality email reply on behalf of the user. "
        "Reply ONLY with the email body text — no subject line, no metadata, no commentary.",
    ]

    if tone == TonePreference.AUTO:
        # Try to infer the user's style from their sent emails.
        # If we don't have any samples (e.g. empty sent folder, or fetch failed),
        # fall back to a sensible neutral default.
        style_context = _build_style_context(sent_samples or [])
        if style_context:
            system_parts.append(style_context)
        else:
            system_parts.append("Use a professional yet warm tone.")
    else:
        # User picked a specific tone — use the predefined instruction for it.
        system_parts.append(TONE_INSTRUCTIONS[tone])

    # Add the user's custom instructions if they've set any.
    # These are appended after the tone so they can override tone behaviour
    # if the user wants ("always use bullet points" overrides "concise").
    if preferences and preferences.custom_instructions:
        system_parts.append(
            f"Additional instructions: {preferences.custom_instructions}"
        )

    # Add the user's signature so Claude appends it to every draft.
    # We tell Claude explicitly to "always end with" it so it doesn't
    # accidentally put it in the middle of the email.
    if preferences and preferences.signature:
        system_parts.append(
            f"Always end your reply with this signature:\n{preferences.signature}"
        )

    system_prompt = "\n\n".join(system_parts)

    # ── Build the user message ────────────────────────────────────────────────
    # This is the actual email Claude needs to reply to.
    # We format it clearly so Claude understands the context and who sent it.
    # We prefer 'body' over 'snippet' since the snippet is just a short preview.
    user_message = (
        f"Please draft a reply to the following email.\n\n"
        f"From: {incoming_email.get('sender', 'Unknown')}\n"
        f"Subject: {incoming_email.get('subject', '(no subject)')}\n"
        f"Date: {incoming_email.get('date', '')}\n\n"
        f"{incoming_email.get('body', incoming_email.get('snippet', ''))}"
    )

    # ── Call Claude ───────────────────────────────────────────────────────────
    # max_tokens=1000 is enough for most email replies while keeping costs low.
    # A typical professional email reply is 100-300 tokens. If you find Claude
    # is cutting off long replies, increase this — but be aware it affects cost.
    response = _client.messages.create(
        model=settings.AI_MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    draft_body = response.content[0].text.strip()
    return draft_body, tone