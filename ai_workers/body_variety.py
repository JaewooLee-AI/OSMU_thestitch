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
    # 소제목 줄은 문장이 아닙니다 — 첫 문장 자리에 소제목이 잡히면 '최근 글과
    # 다른 첫 문장' 지시가 소제목끼리의 비교가 됩니다.
    from ai_workers.body_format import heading_text

    text = "\n".join(
        line for line in (content or "").split("\n") if heading_text(line) is None
    )
    text = IMAGE_TAG_RE.sub("\n", text)
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


# --- stock phrases ------------------------------------------------------------
# The 4-gram overlap above catches a post that copies another. It does not
# catch the more common failure: posts that say different things in the same
# stock phrases. Over eleven posts '가장 행복한 날 입었던' appeared in eight,
# '정성' in ten, '소중한' in nine, and eight ended on the same 옷장 속 한복 기부
# line — so posts about similar products read as one post, and the marketer
# rewrote them by hand. Those phrases are found here, from the blog's own
# recent posts, and the draft is told not to use them this time.
#
# Brand-agnostic: nothing is listed by hand. A phrase qualifies by recurring
# across recent posts, and anything containing a brand term or SEO keyword is
# skipped — those are meant to repeat.

# A phrase is "stock" once it shows up in this share of recent posts…
STOCK_PHRASE_SHARE = 0.4
# …and in at least this many of them, so two posts can't define a habit.
STOCK_PHRASE_MIN_POSTS = 3
STOCK_PHRASE_LIMIT = 8
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")
# Grammar, not style: a phrase made only of these ('수 있습니다') recurs in
# every Korean text and banning it would just make sentences awkward.
_FUNCTION_WORDS = {
    "수", "있습니다", "있어요", "있는", "있고", "합니다", "해요", "하는", "하고", "드립니다",
    "드려요", "됩니다", "돼요", "되는", "것", "것이", "거예요", "그", "이", "더", "및", "등", "또",
}


def _word_ngrams(text: str, sizes=(2, 3, 4)) -> set:
    from ai_workers.body_format import strip_markers

    text = IMAGE_TAG_RE.sub("\n", strip_markers(text or ""))
    text = _SOURCE_FOOTER_RE.sub("", text)
    grams = set()
    for sentence in _SENTENCE_RE.findall(text):
        words = _WORD_RE.findall(sentence)
        for n in sizes:
            for i in range(len(words) - n + 1):
                grams.add(" ".join(words[i:i + n]))
    return grams


def stock_phrases(history: List[dict], protected: List[str] | None = None) -> List[str]:
    """Multi-word phrases that recur across recent posts, longest first."""
    bodies = [post.get("content") or "" for post in history if (post.get("content") or "").strip()]
    if len(bodies) < STOCK_PHRASE_MIN_POSTS:
        return []
    need = max(STOCK_PHRASE_MIN_POSTS, int(len(bodies) * STOCK_PHRASE_SHARE + 0.999))
    counts: dict = {}
    for body in bodies:
        for gram in _word_ngrams(body):
            counts[gram] = counts.get(gram, 0) + 1
    guard = [t.lower() for t in (protected or []) if t and len(t) >= 2]
    frequent = [
        gram for gram, n in counts.items()
        if n >= need and len(gram.replace(" ", "")) >= 5
        and not all(w in _FUNCTION_WORDS for w in gram.split())
        and not any(t in gram.lower() or gram.lower() in t for t in guard)
    ]
    # Longest first, then drop phrases contained in an already-kept one —
    # '행복한 날 입었던' adds nothing once '가장 행복한 날 입었던' is listed.
    frequent.sort(key=len, reverse=True)
    kept: List[str] = []
    for gram in frequent:
        if any(gram in k for k in kept):
            continue
        kept.append(gram)
    kept.sort(key=lambda g: (-counts[g], -len(g)))
    return kept[:STOCK_PHRASE_LIMIT]


def stock_phrases_used(content: str, phrases: List[str]) -> List[str]:
    """Which of `phrases` the new post still uses — report only."""
    grams = _word_ngrams(content)
    return [p for p in phrases if p in grams]

