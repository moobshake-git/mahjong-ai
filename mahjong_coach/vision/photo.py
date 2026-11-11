"""Photo mode: read the player's own tiles from one picture using Claude.

This is phase 1 of the camera plan: no model to train. The page sends a photo
taken from behind the player's shoulder; Claude lists the hand tiles left to
right plus any bonus tiles, using the coach's tile codes. The page shows the
result for the player to confirm or fix before anything is used.

Optional: needs ``pip install anthropic`` and an Anthropic API key
(``ANTHROPIC_API_KEY`` in ``.env``). Without them the rest of the app works
as before and the page says photo mode is off.

Configuration:
    ANTHROPIC_API_KEY   your Anthropic key (or another credential the SDK finds)
    VISION_MODEL        default "claude-opus-5-5"
"""
from __future__ import annotations

import base64
import json
import os

from .tracker import norm_tile

DEFAULT_MODEL = "claude-opus-5-5"
MAX_IMAGE_BYTES = 5 * 1024 * 1024          # the API's limit for one base64 image
MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")

TILE_CODES = ([f"{n}{s}" for s in "mps" for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)])
BONUS_CODES = ["f1", "f2", "f3", "f4", "s1", "s2", "s3", "s4", "cat", "rat", "rooster", "centipede"]

PROMPT = """This photo is taken from behind a mahjong player's shoulder (Singapore rules, 148-tile set).
Read only the PLAYER'S OWN standing tiles in the row nearest the camera, from left to right, and the
player's own bonus tiles (flowers, seasons, animals) laid face up next to them. Ignore discards in the
middle of the table and other players' tiles.

Codes: 1m-9m Characters (萬), 1p-9p Dots (筒), 1s-9s Bamboo (索; 1s is usually a bird),
1z East 2z South 3z West 4z North, 5z White dragon (blank or framed), 6z Green dragon (發), 7z Red dragon (中).
Bonus: f1-f4 flowers (plum, orchid, chrysanthemum, bamboo; red number), s1-s4 seasons (spring, summer,
autumn, winter; blue number), cat, rat, rooster, centipede.

If you can't read a tile with confidence, give your best guess and put its position (0-based, in the
hand list) in "unsure". Put anything the player should check (glare, a tile cut off, a tile count that
isn't 13 or 14) in "note"; otherwise leave it empty."""

SCHEMA = {
    "type": "object",
    "properties": {
        "hand": {"type": "array", "items": {"type": "string", "enum": TILE_CODES}},
        "bonus": {"type": "array", "items": {"type": "string", "enum": BONUS_CODES}},
        "unsure": {"type": "array", "items": {"type": "integer"}},
        "note": {"type": "string"},
    },
    "required": ["hand", "bonus", "unsure", "note"],
    "additionalProperties": False,
}


class PhotoError(Exception):
    """A problem to show the player (bad image, no key, API down...)."""


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def model() -> str:
    return os.environ.get("VISION_MODEL", DEFAULT_MODEL)


def read_photo(image: bytes, media_type: str, client=None) -> dict:
    """Ask Claude which tiles are in the player's hand. ``client`` is for tests."""
    if media_type not in MEDIA_TYPES:
        raise PhotoError("Send a JPEG, PNG, WebP or GIF photo.")
    if not image:
        raise PhotoError("The photo was empty.")
    if len(image) > MAX_IMAGE_BYTES:
        raise PhotoError("That photo is over 5 MB. Take it at a lower resolution and try again.")

    if client is None:
        try:
            import anthropic
        except ImportError:
            raise PhotoError("Photo mode needs the Anthropic SDK: run  pip install anthropic") from None
        try:
            client = anthropic.Anthropic()
        except Exception as e:  # e.g. no credentials found
            raise PhotoError(_explain(e, anthropic)) from e
    else:
        anthropic = None

    request = dict(
        model=model(),
        max_tokens=16000,
        # If the main model declines, the API retries on a fallback model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(image).decode("ascii")}},
            {"type": "text", "text": PROMPT},
        ]}],
    )
    try:
        response = client.beta.messages.create(**request)
    except Exception as e:  # map SDK errors to something the player can act on
        raise PhotoError(_explain(e, anthropic)) from e

    if response.stop_reason == "refusal":
        raise PhotoError("Claude declined to read this photo. Try another picture of just your tiles.")
    if response.stop_reason == "max_tokens":
        raise PhotoError("The answer was cut off. Please try again.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise PhotoError("Claude didn't return a tile list. Please try again.")
    data = json.loads(text)

    hand = [t for t in (norm_tile(x) for x in data.get("hand", [])) if t and len(t) == 2 and t[1] in "mpsz"]
    bonus = []
    for x in data.get("bonus", []):
        t = norm_tile(x)
        if t in BONUS_CODES and t not in bonus:
            bonus.append(t)
    unsure = sorted({i for i in data.get("unsure", []) if isinstance(i, int) and 0 <= i < len(hand)})
    return {"hand": hand, "bonus": bonus, "unsure": unsure, "note": data.get("note", ""),
            "model": getattr(response, "model", model())}


def _explain(e: Exception, anthropic) -> str:
    if anthropic is None:
        return f"Photo reading failed: {e}"
    if isinstance(e, anthropic.AuthenticationError):
        return "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This Anthropic key can't use that model. Check VISION_MODEL in .env."
    if isinstance(e, anthropic.RateLimitError):
        return "Too many photos at once. Wait a few seconds and try again."
    if isinstance(e, anthropic.BadRequestError):
        return f"The photo request was rejected: {e.message}"
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude had a problem ({e.status_code}). Try again shortly."
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach Claude. Check the internet connection."
    if "credentials" in str(e).lower() or "api_key" in str(e).lower():
        return "No Anthropic API key found. Add ANTHROPIC_API_KEY to .env to use photo mode."
    return f"Photo reading failed: {e}"
