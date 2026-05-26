"""Token counting utility for accurate LLM context window tracking."""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _ENCODER = None
    logger.warning("tiktoken not installed. Falling back to char-based token counting.")

def count_tokens(text: str) -> int:
    """Accurately count tokens using cl100k_base (OpenAI) or fallback heuristic."""
    if not text:
        return 0
    if _ENCODER:
        try:
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    # Fallback: non-ASCII chars usually map to ~2 tokens, ASCII to ~0.3
    return int(sum(2.0 if ord(c) > 127 else 0.33 for c in text))

def count_message_tokens(messages: List[Dict[str, Any]]) -> int:
    """Count total tokens in a list of chat messages."""
    total = 0
    for m in messages:
        # Add buffer for message framing (role, etc.)
        total += 4
        content = m.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            # Handle vision blocks
            for block in content:
                if block.get("type") == "text":
                    total += count_tokens(block.get("text", ""))
                elif block.get("type") == "image_url":
                    # Vision tokens are expensive, estimate ~85 per tile, rough estimate 1000
                    total += 1000
    total += 3  # Assistant reply preamble
    return total
