"""Live camera: read the whole table from one video frame using Claude.

The page shows the camera (a phone or webcam behind your shoulder) and sends
a still frame only when the table has stopped moving. Claude lists every tile
it can see, sorted into the same zones and seats the tracker uses; the
tracker then needs two reads that agree before it accepts a change.

There's no model to train for this first version. Later a small detector
running on the laptop can replace this module and feed the same tracker.

Optional: needs ``pip install anthropic`` and an Anthropic API key
(``ANTHROPIC_API_KEY`` in ``.env``). Each read is one Claude request with one
image, so cost depends on the model: set ``VISION_MODEL`` to choose.

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
MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp")

TILE_CODES = ([f"{n}{s}" for s in "mps" for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)])
BONUS_CODES = ["f1", "f2", "f3", "f4", "s1", "s2", "s3", "s4", "cat", "rat", "rooster", "centipede"]
OTHER_SEATS = ("left", "across", "right")

PROMPT = """This is a frame from a phone camera mounted behind a mahjong player's shoulder, looking down at
the table (Singapore rules, 148-tile set). "Me" is the player whose shoulder the camera is behind.
The other players are: "left" (beside me, on my left), "across" (opposite me, called Oppo) and
"right" (beside me, on my right).

List every tile face you can read:
- hand: MY standing tiles in the row nearest the camera, left to right (13 or 14 tiles, or fewer if I
  have called sets). Include a drawn tile set slightly apart at the end.
- bonus: MY flowers, seasons and animals laid face up near me.
- my_melds: MY face-up called sets (pong/kong/chow), one list per set.
- discards: face-up discarded tiles, by the player who threw them. Each player's discards are usually
  in rows in front of them, towards the middle of the table.
- melds: other players' face-up called sets, one list per set, by player.
- hands_over_table: true if an arm or hand covers part of the table, so the view isn't complete.

Codes: 1m-9m Characters (萬), 1p-9p Dots (筒), 1s-9s Bamboo (索; 1s is usually a bird),
1z East 2z South 3z West 4z North, 5z White dragon (blank or framed), 6z Green dragon (發), 7z Red dragon (中).
Bonus: f1-f4 flowers (plum, orchid, chrysanthemum, bamboo; red number), s1-s4 seasons (spring, summer,
autumn, winter; blue number), cat, rat, rooster, centipede.

Only list tiles whose face you can actually see; skip face-down tiles and the wall. Put anything I
should check (glare, a tile cut off, unsure which player a discard belongs to) in "note"."""

_TILE = {"type": "string", "enum": TILE_CODES}
_SETS = {"type": "array", "items": {"type": "array", "items": _TILE}}
SCHEMA = {
    "type": "object",
    "properties": {
        "hand": {"type": "array", "items": _TILE},
        "bonus": {"type": "array", "items": {"type": "string", "enum": BONUS_CODES}},
        "my_melds": _SETS,
        "discards": {"type": "object", "properties": {s: {"type": "array", "items": _TILE} for s in ("me",) + OTHER_SEATS},
                     "required": ["me", *OTHER_SEATS], "additionalProperties": False},
        "melds": {"type": "object", "properties": {s: _SETS for s in OTHER_SEATS},
                  "required": list(OTHER_SEATS), "additionalProperties": False},
        "hands_over_table": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["hand", "bonus", "my_melds", "discards", "melds", "hands_over_table", "note"],
    "additionalProperties": False,
}


class ReadError(Exception):
    """A problem to show the player (bad image, no key, API down...)."""


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def model() -> str:
    return os.environ.get("VISION_MODEL", DEFAULT_MODEL)


def read_frame(image: bytes, media_type: str, client=None) -> dict:
    """Ask Claude what's on the table. Returns a tracker frame plus the raw read.
    ``client`` is for tests."""
    if media_type not in MEDIA_TYPES:
        raise ReadError("Send a JPEG, PNG or WebP frame.")
    if not image:
        raise ReadError("The frame was empty.")
    if len(image) > MAX_IMAGE_BYTES:
        raise ReadError("That frame is over 5 MB. Lower the camera resolution.")

    if client is None:
        try:
            import anthropic
        except ImportError:
            raise ReadError("The live camera needs the Anthropic SDK: run  pip install anthropic") from None
        try:
            client = anthropic.Anthropic()
        except Exception as e:  # e.g. no credentials found
            raise ReadError(_explain(e, anthropic)) from e
    else:
        anthropic = None

    try:
        response = client.beta.messages.create(
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
    except Exception as e:  # map SDK errors to something the player can act on
        raise ReadError(_explain(e, anthropic)) from e

    if response.stop_reason == "refusal":
        raise ReadError("Claude declined to read this frame. Point the camera at just the table.")
    if response.stop_reason == "max_tokens":
        raise ReadError("The answer was cut off. Trying again on the next frame.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ReadError("Claude didn't return a tile list.")
    data = json.loads(text)
    return {"frame": to_frame(data), "read": data, "model": getattr(response, "model", model())}


def to_frame(data: dict) -> dict:
    """Convert Claude's table read into the tracker's frame format."""
    dets = []

    def add(tiles, zone, seat, group=None):
        for i, t in enumerate(tiles or []):
            t = norm_tile(t)
            if t is None:
                continue
            d = {"tile": t, "zone": zone, "seat": seat, "conf": 0.9, "box": [i, 0, 1, 1]}
            if group is not None:
                d["group"] = group
                d["box"] = [group * 10 + i, 0, 1, 1]
            dets.append(d)

    add(data.get("hand"), "hand", "me")
    add(data.get("bonus"), "bonus", "me")
    for g, meld in enumerate(data.get("my_melds") or []):
        add(meld, "meld", "me", group=g)
    for seat, tiles in (data.get("discards") or {}).items():
        if seat in ("me",) + OTHER_SEATS:
            add(tiles, "discard", seat)
    for seat, sets in (data.get("melds") or {}).items():
        if seat in OTHER_SEATS:
            for g, meld in enumerate(sets or []):
                add(meld, "meld", seat, group=g)
    return {"detections": dets, "motion": bool(data.get("hands_over_table"))}


def _explain(e: Exception, anthropic) -> str:
    if anthropic is None:
        return f"Reading the frame failed: {e}"
    if isinstance(e, anthropic.AuthenticationError):
        return "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This Anthropic key can't use that model. Check VISION_MODEL in .env."
    if isinstance(e, anthropic.RateLimitError):
        return "Reading too fast for the API's rate limit. Slowing down."
    if isinstance(e, anthropic.BadRequestError):
        return f"The frame was rejected: {e.message}"
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude had a problem ({e.status_code}). Trying again shortly."
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach Claude. Check the internet connection."
    if "credentials" in str(e).lower() or "api_key" in str(e).lower():
        return "No Anthropic API key found. Add ANTHROPIC_API_KEY to .env to use the live camera."
    return f"Reading the frame failed: {e}"
