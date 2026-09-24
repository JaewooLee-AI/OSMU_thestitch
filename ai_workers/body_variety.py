"""Keeps post bodies from converging on each other — title_variety.py's
counterpart for the body.

Posts about similar products are drafted from the same brand kit, the same
facts and the same sample post, and came out close enough that a marketer
rewrote four of them by hand. Beyond the extra work, Naver scores posts from
one blog that repeat each other as 유사문서 and lowers their exposure.

Two layers, same shape as title_variety:

1. Prevention — the drafting prompt lists recent posts' first and last
   sentences as "don't repeat these" (prompt_builder.recent_posts_block).
2. Measurement — the finished body is compared with recent posts and the
   report says how much of it already appeared in the closest one. It only
   *reports*: rewriting for variety would need the details that make this
   product different, and those have to come from the marketer's memo.

History is the campaigns table, so a deleted campaign stops counting — unlike
title_history, which deliberately outlives campaigns. Bodies are too large to
keep a second copy of forever, and the last few posts on the blog are the
ones a near-duplicate would be judged against.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ai_workers.photo_placement import IMAGE_TAG_RE

# Calibrated on real posts from this app: unrelated posts share 0~10% of
# their 4-character runs, two holiday-schedule notices from the same brand
# 25%. At or above this, the new post reads like a variation of an old one.
SIMILARITY_THRESHOLD = 0.25

# Character 4-grams on text stripped of spaces and punctuation — no Korean
# tokenizer needed, and short enough to survive particle/ending changes.
SHINGLE = 4

# Posts compared against. The last several are what a reader (and Naver's
# same-blog duplicate check) sees next to the new one.
HISTORY_LIMIT = 10

_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣]+")
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
_SOURCE_FOOTER_RE = re.compile(r"\[원문 기사 출처:[^\]]*\]")


def _normalize(text: str) -> str:
    text = IMAGE_TAG_RE.sub(" ", text or "")
    text = _SOURCE_FOOTER_RE.sub(" ", text)
    return _NON_WORD_RE.sub("", text.lower())


def _shingles(text: str) -> set:
    norm = _normalize(text)
    if len(norm) < SHINGLE:
        return set()
    return {norm[i:i + SHINGLE] for i in range(len(norm) - SHINGLE + 1)}


def overlap(new: str, old: str) -> float:
    """Share of the new post's 4-grams that also occur in the old one, 0~1.

    Asymmetric on purpose: "how much of *this* post has been said before" is
    the question, and a short notice fully contained in a long post is a
    repeat even though a symmetric Jaccard would call it small.
    """
    a, b = _shingles(new), _shingles(old)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def closest_previous(content: str, history: List[dict]) -> Tuple[Optional[dict], float]:
    best, best_score = None, 0.0
    for post in history:
        score = overlap(content, post.get("content") or "")
        if score > best_score:
            best, best_score = post, score
    return best, best_score


def _sentences(content: str) -> List[str]:
    text = IMAGE_TAG_RE.sub("\n", content or "")
    text = _SOURCE_FOOTER_RE.sub("", text)
    return [s.strip() for s in _SENTENCE_RE.findall(text) if len(s.strip()) >= 5]


def prompt_history(history: List[dict]) -> List[dict]:
    """history rows -> {title, opening, closing} for recent_posts_block."""
    out = []
    for post in history:
        sentences = _sentences(post.get("content") or "")
        if not sentences:
            continue
        out.append({
            "title": (post.get("title") or "(제목 없음)").strip(),
            "opening": sentences[0][:80],
            "closing": sentences[-1][:80],
        })
    return out


def report(content: str, history: List[dict]) -> dict:
    """The report entry the workbench shows."""
    if not history:
        return {"checked": False}
    similar, score = closest_previous(content, history)
    result = {"checked": True, "score": round(score, 2), "compared": len(history)}
    if similar is not None and score >= SIMILARITY_THRESHOLD:
        result["similar_to"] = similar.get("title") or "(제목 없음)"
    return result
