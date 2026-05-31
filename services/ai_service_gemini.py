"""
services/ai_service.py  (Gemini version)

Responsible for generating email reply drafts using Google Gemini.

This is the Gemini alternative to the Anthropic Claude version. The logic
is identical — the only difference is the API client and how the prompt
is structured. Gemini uses a single combined prompt rather than separate
system/user messages like Claude does.

The most interesting feature here is AUTO tone. Instead of asking users
to describe their writing style, we fetch their recent sent emails and
let Gemini figure it out. It reads those samples and picks up on patterns like:
- How they greet people ("Hi", "Hello", "Hey", "Dear")
- How formal or casual their language is
- Whether they write long paragraphs or short punchy sentences
- How they sign off ("Best", "Thanks", "Regards", "Cheers")

The result is a draft that sounds like the user wrote it, not like a chatbot.

Why Gemini instead of Claude?
Gemini has a generous free tier (1,500 requests/day on Flash models) which
makes it practical for development and small-scale use without any upfront cost.
The gemini-1.5-flash-8b model is also very fast, which helps with response times.

How the prompt is structured (Gemini uses one combined prompt):
    Role + behaviour instructions
    + tone instructions (explicit or inferred from sent emails)
    + user's custom instructions (if any)
    + user's signature (if set)
    + the actual email to reply to
"""

from google import genai

from config import get_settings
from models import TonePreference, UserPreference

settings = get_settings()

# Single shared client — thread-safe and designed to be reused across requests.
client = genai.Client(api_key=settings.GEMINI_API_KEY)

# Explicit tone instructions injected into the prompt when the user picks
# a specific tone. These give Gemini clear behavioural direction rather than
# relying on vague words like "be professional".
TONE_INSTRUCTIONS = {
    TonePreference.FORMAL:   "Use formal, professional language. Avoid contractions and colloquialisms.",
    TonePreference.CONCISE:  "Be brief and to-the-point. Use short sentences. Avoid filler words.",
    TonePreference.FRIENDLY: "Use a warm, conversational tone. Light and approachable, but still professional.",
    TonePreference.AUTO:     "",  # AUTO tone is handled dynamically via style inference below
}


def _build_style_context(sent_samples: list[dict]) -> str:
    """
    Build a prompt section that teaches Gemini the user's writing style.

    We pass the user's recent sent emails as examples for Gemini to study.
    Even a few samples are enough for it to pick up on consistent patterns —
    things like whether someone writes "Thanks for reaching out" vs "Thank you
    for your message" reveal a lot about their communication style.

    We cap each email at 500 characters to keep the overall prompt a
    reasonable size. Style signals (greeting, tone, sign-off) are almost
    always in the first and last few lines anyway, so we're not losing
    much by truncating the middle of long emails.

    Returns an empty string if no samples are available, which causes
    the caller to fall back to a generic professional tone instead.
    """
    if not sent_samples:
        return ""

    # Separate each sample with a visible divider so Gemini understands
    # where one email ends and the next one begins.
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
    Generate a reply draft for an incoming email using Gemini.

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

    # ── Build the prompt ──────────────────────────────────────────────────────
    # Unlike Claude which uses separate system/user messages, Gemini takes
    # a single combined prompt. We build it in parts and join at the end
    # so it's easy to conditionally include or exclude sections.

    prompt_parts = [
        "You are Draftly, an expert email assistant. "
        "Your job is to write a high-quality email reply on behalf of the user. "
        "Reply ONLY with the email body text — no subject line, no metadata, no commentary.\n\n",
    ]

    if tone == TonePreference.AUTO:
        # Try to infer the user's natural writing style from sent email samples.
        # Fall back to a sensible neutral tone if we have nothing to work with.
        style_context = _build_style_context(sent_samples or [])
        if style_context:
            prompt_parts.append(style_context)
        else:
            prompt_parts.append("Use a professional yet warm tone.\n\n")
    else:
        # User chose a specific tone — use the predefined instructions for it.
        prompt_parts.append(TONE_INSTRUCTIONS[tone] + "\n\n")

    # Custom instructions override everything else — if the user says
    # "always use bullet points", that applies regardless of the tone setting.
    if preferences and preferences.custom_instructions:
        prompt_parts.append(
            f"Additional instructions: {preferences.custom_instructions}\n\n"
        )

    # Tell Gemini to append the signature at the end of every reply.
    # Being explicit ("always end with") prevents it from putting the
    # signature in the middle or omitting it when the reply is short.
    if preferences and preferences.signature:
        prompt_parts.append(
            f"Always end your reply with this signature:\n{preferences.signature}\n\n"
        )

    # The actual email we need a reply for — placed last so it's the
    # most recent thing in context when Gemini generates the response.
    # We prefer full body over snippet when available.
    prompt_parts.append(
        f"Please draft a reply to the following email.\n\n"
        f"From: {incoming_email.get('sender', 'Unknown')}\n"
        f"Subject: {incoming_email.get('subject', '(no subject)')}\n"
        f"Date: {incoming_email.get('date', '')}\n\n"
        f"{incoming_email.get('body', incoming_email.get('snippet', ''))}"
    )

    full_prompt = "".join(prompt_parts)

    # ── Call Gemini ───────────────────────────────────────────────────────────
    # gemini-1.5-flash-8b is the fastest and cheapest model — well within
    # the free tier limits and plenty capable for email reply generation.
    # Switch to gemini-1.5-pro in AI_MODEL if you want higher quality
    # at the cost of slower responses and higher quota usage.
    response = client.models.generate_content(
        model=settings.AI_MODEL,
        contents=full_prompt,
    )

    draft_body = response.text.strip()
    return draft_body, tone