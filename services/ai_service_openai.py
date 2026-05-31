"""
services/ai_service.py  (OpenAI version)

Responsible for generating email reply drafts using OpenAI's GPT models.

This is the OpenAI alternative to the Anthropic and Gemini versions. The logic
is identical across all three — the only differences are the API client, the
model name, and minor prompt structure details. OpenAI uses the same
system/user message separation as Claude does.

The most interesting feature here is AUTO tone. Instead of asking users to
describe their writing style, we fetch their recent sent emails and let the
model figure it out. It reads those samples and picks up on patterns like:
- How they greet people ("Hi", "Hello", "Hey", "Dear")
- How formal or casual their language is
- Whether they write long paragraphs or short punchy sentences
- How they sign off ("Best", "Thanks", "Regards", "Cheers")

The result is a draft that sounds like the user wrote it, not a generic AI reply.

Why OpenAI?
GPT-4o-mini is fast, cheap, and very capable at structured writing tasks like
email replies. It's a good choice if you already have OpenAI credits or prefer
the OpenAI ecosystem. Note that unlike Gemini, OpenAI has no meaningful free
tier — you'll need to add credits at platform.openai.com before using this.

How the prompt is structured:
    System message — role, behaviour, tone, custom instructions, signature
    User message   — the actual email to reply to

The system/user split is important for OpenAI models. Instructions in the
system message carry more weight and are less likely to be overridden by
content in the email being replied to.
"""

from openai import OpenAI

from config import get_settings
from models import TonePreference, UserPreference

settings = get_settings()

# Single shared client — thread-safe and designed to be reused across requests.
# Creating a new client per request would add unnecessary overhead.
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# Explicit tone instructions injected into the system prompt when the user
# picks a specific tone. More precise than just saying "be formal" —
# these tell the model exactly what behaviours we expect.
TONE_INSTRUCTIONS = {
    TonePreference.FORMAL:   "Use formal, professional language. Avoid contractions and colloquialisms.",
    TonePreference.CONCISE:  "Be brief and to-the-point. Use short sentences. Avoid filler words.",
    TonePreference.FRIENDLY: "Use a warm, conversational tone. Light and approachable, but still professional.",
    TonePreference.AUTO:     "",  # AUTO tone is handled dynamically via style inference below
}


def _build_style_context(sent_samples: list[dict]) -> str:
    """
    Build a prompt section that teaches the model the user's writing style.

    We pass the user's recent sent emails as examples to learn from.
    Even a handful of samples is enough to pick up on consistent patterns —
    things like whether someone writes "Thanks for reaching out" vs "Thank you
    for contacting me" reveal a lot about their communication preferences.

    We cap each email at 500 characters to keep the overall prompt size
    reasonable. Style signals are almost always in the first and last few
    lines (greeting and sign-off), so truncating long email bodies doesn't
    lose much useful information.

    Returns an empty string if no samples are available, which causes
    the caller to fall back to a generic professional tone instead.
    """
    if not sent_samples:
        return ""

    # Separate samples with a visible divider so the model understands
    # where one email ends and the next begins.
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
    Generate a reply draft for an incoming email using OpenAI.

    Args:
        incoming_email: The email to reply to, as returned by gmail_service.
                        Needs at least 'sender', 'subject', and 'body' or 'snippet'.
        preferences:    The user's saved preferences (tone, signature,
                        custom instructions). Can be None if not yet configured.
        sent_samples:   Recent sent emails used for style inference when tone
                        is AUTO. Fetched by draft_service before calling this.
        override_tone:  Tone from the API request, takes priority over preferences.
                        Pass None to use whatever is saved in preferences.

    Returns:
        A tuple of (draft_body, tone_used) where:
        - draft_body is the plain text of the generated reply (ready to send)
        - tone_used is which TonePreference was actually applied
    """
    # Tone priority: request override → saved preference → AUTO fallback
    tone = override_tone or (preferences.tone if preferences else TonePreference.AUTO)

    # ── Build the system message ──────────────────────────────────────────────
    # The system message defines the model's role and behaviour for the whole
    # conversation. In OpenAI's chat format, this is more authoritative than
    # the user message — instructions here are less likely to be contradicted
    # by content in the email being replied to.

    system_parts = [
        "You are Draftly, an expert email assistant. "
        "Your job is to write a high-quality email reply on behalf of the user. "
        "Reply ONLY with the email body text — no subject line, no metadata, no commentary.",
    ]

    if tone == TonePreference.AUTO:
        # Try to infer the user's natural writing style from their sent emails.
        # Fall back to a neutral professional tone if we have no samples.
        style_context = _build_style_context(sent_samples or [])
        if style_context:
            system_parts.append(style_context)
        else:
            system_parts.append("Use a professional yet warm tone.")
    else:
        # User picked a specific tone — inject the predefined instructions for it.
        system_parts.append(TONE_INSTRUCTIONS[tone])

    # Custom instructions are appended after tone so they can override it
    # if needed — "always use bullet points" should win over "be concise".
    if preferences and preferences.custom_instructions:
        system_parts.append(
            f"Additional instructions: {preferences.custom_instructions}"
        )

    # Signature goes in the system message so the model treats it as a
    # hard rule, not a suggestion that might get dropped in short replies.
    if preferences and preferences.signature:
        system_parts.append(
            f"Always end your reply with this signature:\n{preferences.signature}"
        )

    system_prompt = "\n\n".join(system_parts)

    # ── Build the user message ────────────────────────────────────────────────
    # The user message contains the email we need a reply for.
    # Keeping it separate from the system message helps the model distinguish
    # between "what I should do" (system) and "what I'm working with" (user).
    # We prefer full body over snippet when available.
    user_message = (
        f"Please draft a reply to the following email.\n\n"
        f"From: {incoming_email.get('sender', 'Unknown')}\n"
        f"Subject: {incoming_email.get('subject', '(no subject)')}\n"
        f"Date: {incoming_email.get('date', '')}\n\n"
        f"{incoming_email.get('body', incoming_email.get('snippet', ''))}"
    )

    # ── Call OpenAI ───────────────────────────────────────────────────────────
    # max_tokens=1000 covers most email replies comfortably — a typical
    # professional reply is 100-300 tokens. Increase if you find Claude cutting
    # off longer replies, but be mindful of cost.
    #
    # temperature=0.7 gives a good balance between creativity and consistency.
    # Lower (e.g. 0.3) = more predictable, less varied replies.
    # Higher (e.g. 1.0) = more creative but potentially less professional.
    response = client.chat.completions.create(
        model=settings.AI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        max_tokens=1000,
        temperature=0.7,
    )

    draft_body = response.choices[0].message.content.strip()
    return draft_body, tone