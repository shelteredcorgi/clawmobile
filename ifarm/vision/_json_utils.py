"""Utilities for extracting JSON from VLM text responses.

LLMs often wrap JSON in markdown code fences or add surrounding commentary.
These helpers strip that noise and return a clean Python dict.
"""
from __future__ import annotations

import json
import re

from ifarm.exceptions import VisionError

# Match ```json ... ``` or ``` ... ``` blocks
_CODE_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
# Match the first {...} or [...] in a string
_JSON_OBJECT = re.compile(r"(\{[\s\S]*\}|\[[\s\S]*\])")


def parse_vlm_response(text: str, context: str = "") -> dict | list:
    """Extract and parse the first JSON value from a VLM response string.

    Tries strategies in order:
      1. Parse the whole string as JSON (model returned clean JSON)
      2. Extract from a markdown ```json ... ``` code fence
      3. Find the first {...} or [...] block anywhere in the text

    Args:
        text: Raw text response from a VLM.
        context: Human-readable description for error messages (e.g. model name).

    Returns:
        Parsed Python dict or list.

    Raises:
        VisionError: If no valid JSON can be extracted.
    """
    stripped = text.strip()

    # Strategy 1 — whole string is JSON
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strategy 2 — markdown code fence
    fence_match = _CODE_FENCE.search(stripped)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3 — first JSON object/array in the text
    obj_match = _JSON_OBJECT.search(stripped)
    if obj_match:
        try:
            return json.loads(obj_match.group(1))
        except json.JSONDecodeError:
            pass

    raise VisionError(
        f"Could not extract JSON from VLM response"
        + (f" ({context})" if context else "")
        + f". Raw response (first 200 chars): {stripped[:200]!r}"
    )
