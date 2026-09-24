"""Framework-free helpers shared by the Flet channel simulators.

These used to be imported from the Streamlit-era top-level `simulators/`
package (its HTML templates are gone with Streamlit); only the pure logic the
Flet renderers actually use lives on here.
"""
from __future__ import annotations

from typing import List, Tuple

from ai_workers.photo_placement import split_segments

TWEET_LIMIT = 140  # Korean-text working limit, matching ai_workers/x_thread_writer.py
MAX_MEDIA = 4


def paragraphs(text: str) -> List[str]:
    return [p for p in (text or "").split("\n") if p.strip()]


def content_blocks(content: str) -> List[Tuple[str, str]]:
    """('text', paragraph) / ('image', rel_path) in document order."""
    blocks: List[Tuple[str, str]] = []
    for kind, value in split_segments(content):
        if kind == "image":
            blocks.append(("image", value))
        else:
            for para in paragraphs(value):
                blocks.append(("text", para))
    return blocks


def all_images(content: str, storage_file_paths: List[str]) -> List[str]:
    """Every photo the post will show, tagged ones first in document order,
    then any attached-but-untagged ones (which the blog view appends as a
    trailing gallery)."""
    tagged = [value for kind, value in split_segments(content) if kind == "image"]
    extras = [p for p in (storage_file_paths or []) if p not in tagged]
    return tagged + extras


def split_tweet(text: str) -> List[str]:
    """Chunks an over-length tweet the way X's composer would, preferring a
    sentence boundary so the break doesn't land mid-word."""
    text = (text or "").strip()
    if len(text) <= TWEET_LIMIT:
        return [text] if text else []

    chunks, remaining = [], text
    while len(remaining) > TWEET_LIMIT:
        window = remaining[:TWEET_LIMIT]
        cut = max(window.rfind("。"), window.rfind("."), window.rfind("!"), window.rfind("?"), window.rfind("\n"))
        if cut < TWEET_LIMIT // 2:
            cut = window.rfind(" ")
        if cut < TWEET_LIMIT // 2:
            cut = TWEET_LIMIT - 1
        chunks.append(remaining[:cut + 1].strip())
        remaining = remaining[cut + 1:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks
