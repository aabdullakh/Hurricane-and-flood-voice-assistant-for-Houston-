"""Backboard conversational memory (optional, OFF unless ENABLE_BACKBOARD=1).

STATUS: UNTESTED against the live service. Written from the Backboard docs:
  https://docs.backboard.io/sdk/first-message   https://docs.backboard.io/sdk/assistants   https://docs.backboard.io/concepts/memory
Backboard scopes memory per ASSISTANT, not per user, and the docs do not describe per-user scoping.
So we create one assistant per household (keyed by the hashed phone number). [VERIFY] with a Backboard mentor.

Hard safety facts (language, oxygen, mobility, pets) live in OUR database (see db.Profile). Backboard is for
softer conversational memory, so a memory failure never loses something critical.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .config import settings
from .db import Profile

log = logging.getLogger("memory")

SYSTEM_PROMPT = (
    "You keep brief notes about one household for a flood-preparedness phone assistant. "
    "Store only what the caller chose to share. Be concise."
)


def _client():
    from backboard import BackboardClient  # pip install backboard-sdk

    return BackboardClient(api_key=settings.backboard_api_key)


async def _ensure_assistant(session: Session, profile: Profile) -> str | None:
    if profile.backboard_assistant_id:
        return profile.backboard_assistant_id
    client = _client()
    assistant = await client.create_assistant(name=f"household-{profile.caller_hash[:10]}", system_prompt=SYSTEM_PROMPT)
    profile.backboard_assistant_id = assistant.assistant_id
    session.add(profile)
    session.commit()
    return profile.backboard_assistant_id


async def remember(session: Session, profile: Profile, text: str) -> bool:
    if not settings.enable_backboard or not settings.backboard_api_key:
        return False
    try:
        aid = await _ensure_assistant(session, profile)
        await _client().send_message(text, assistant_id=aid, memory="Auto")
        return True
    except Exception as e:
        log.warning("backboard remember failed: %s", e)
        return False


async def recall(session: Session, profile: Profile) -> str | None:
    if not settings.enable_backboard or not settings.backboard_api_key or not profile.backboard_assistant_id:
        return None
    try:
        reply = await _client().send_message(
            "In two short sentences, what do you remember about this household?",
            assistant_id=profile.backboard_assistant_id,
            memory="Readonly",
        )
        return getattr(reply, "content", None)
    except Exception as e:
        log.warning("backboard recall failed: %s", e)
        return None
