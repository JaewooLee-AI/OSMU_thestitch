"""Deterministic format checks for the Instagram, X, Shorts and Naver-tag outputs.

The Naver path already follows this project's "verify, don't just instruct"
rule twice over (compliance guardrail, SEO density). The social writers did
not: `TWEET_MAX_CHARS` and the 125-character fold existed only as sentences
inside a system prompt, and nothing looked at what came back.

That gap matters more than the Naver one, because the failure is harder:
a missed keyword costs some ranking, but a 281-character tweet **cannot be
posted at all** — the marketer only finds out when X rejects the paste.

Everything here is deterministic. None of these checks needs a model: an
over-long tweet is split on sentence boundaries, an over-long hashtag list is
trimmed. Where a fix would require rewriting prose (a hook that runs past the
fold, too few hashtags), the issue is reported rather than guessed at, so the
marketer can decide — the same reason the compliance auditor explains instead
of silently rewriting.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Tuple

# X's 280 is a *weighted* budget (twitter-text v3), not a character count:
# code points in the ranges below weigh 1, everything else — every Hangul
# syllable, CJK, emoji — weighs 2, and any URL weighs 23 however long it is.
# A tweet in Korean therefore holds about 140 characters, not 280. These
# limits used to be compared against len(), so 150~240-character Korean
# tweets passed this check and were then refused by X.
#
# The writers aim at the soft limit, leaving room for a hashtag or an emoji
# the marketer adds before posting.
TWEET_SOFT_MAX = 260   # ≈130 Korean characters
TWEET_HARD_MAX = 280   # ≈140 Korean characters — X refuses anything longer

_X_LIGHT_RANGES = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))
_X_URL_RE = re.compile(r"https?://\S+")
X_URL_WEIGHT = 23


def _x_char_weight(ch: str) -> int:
    cp = ord(ch)
    return 1 if any(lo <= cp <= hi for lo, hi in _X_LIGHT_RANGES) else 2


def x_weighted_length(text: str) -> int:
    """Length the way X counts it. Slightly conservative on multi-code-point
    emoji (each code point counts 2 here, X counts the whole sequence as 2) —
    over-counting can only split a tweet early, never let one through that X
    would refuse."""
    text = unicodedata.normalize("NFC", text or "")
    urls = _X_URL_RE.findall(text)
    rest = _X_URL_RE.sub("", text)
    return len(urls) * X_URL_WEIGHT + sum(_x_char_weight(ch) for ch in rest)


def _x_take(text: str, limit: int) -> str:
    """Longest prefix of `text` within `limit` weighted units."""
    total, out = 0, []
    for ch in text:
        w = _x_char_weight(ch)
        if total + w > limit:
            break
        total += w
        out.append(ch)
    return "".join(out)

# Instagram collapses the caption behind "더보기" after ~125 characters or
# two lines, whichever comes first — a caption that opens with a short line
# and a line break shows even less.
IG_FOLD = 125
IG_FOLD_LINES = 2
IG_HASHTAG_MIN = 8
IG_HASHTAG_MAX = 15

# Shorts: on-screen text has to sit in the band between the top and the
# button/caption dead zones (see shorts_writer / the simulator overlay).
SHORTS_CAPTION_MAX = 20
SHORTS_TITLE_MAX = 40

# Naver's 발행 popup: the writer is asked for 7~12 tags.
NAVER_TAG_MIN = 7
NAVER_TAG_MAX = 12


def instagram_preview(caption: str) -> str:
    """The part of a caption visible before "더보기" — the single definition
    both the check below and the simulator's fold line use."""
    caption = caption or ""
    lines = caption.split("\n")
    head = "\n".join(lines[:IG_FOLD_LINES])
    return head[:IG_FOLD]

# Korean sentence enders, kept with the sentence they close.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?。？！])\s+|(?<=[다요])\.\s*")


def _split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in _SENTENCE_END_RE.split(text) if p and p.strip()]
    return parts or [text.strip()]


def _pack(sentences: List[str], limit: int) -> List[str]:
    """Greedily groups sentences into chunks within `limit` X-weighted units."""
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if x_weighted_length(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single sentence longer than the limit has no clean break left;
        # hard-wrap it rather than emit something unpostable.
        while x_weighted_length(sentence) > limit:
            head = _x_take(sentence, limit)
            chunks.append(head)
            sentence = sentence[len(head):]
        current = sentence
    if current:
        chunks.append(current)
    return chunks


def split_tweet(text: str) -> List[str]:
    """One tweet split to fit, the same way validate_tweets does — shared with
    the X simulator so the preview can never disagree with the check."""
    text = (text or "").strip()
    if not text:
        return []
    if x_weighted_length(text) <= TWEET_SOFT_MAX:
        return [text]
    return _pack(_split_sentences(text), TWEET_SOFT_MAX)


def validate_tweets(tweets: List[str]) -> Tuple[List[str], List[Dict]]:
    """Returns (tweets, issues) with any over-limit tweet split in place."""
    fixed: List[str] = []
    issues: List[Dict] = []
    for idx, tweet in enumerate(tweets, start=1):
        weight = x_weighted_length(tweet)
        if weight <= TWEET_SOFT_MAX:
            fixed.append(tweet)
            continue
        pieces = split_tweet(tweet)
        issues.append(
            {
                "level": "fixed" if weight <= TWEET_HARD_MAX else "blocked",
                "message": (
                    f"{idx}번 트윗이 X 기준 {weight}/{TWEET_HARD_MAX}"
                    f"(한글 1자 = 2)로 권장 {TWEET_SOFT_MAX}을 넘어 {len(pieces)}개로 나눴습니다."
                    + (" 나누지 않았다면 X가 게시를 거부했을 길이입니다." if weight > TWEET_HARD_MAX else "")
                ),
            }
        )
        fixed.extend(pieces)
    return fixed, issues


def validate_instagram(caption: str, hashtags: List[str]) -> Tuple[str, List[str], List[Dict]]:
    """Returns (caption, hashtags, issues). The caption is never rewritten —
    only measured — because trimming prose to fit the fold would cut the hook
    this check exists to protect."""
    issues: List[Dict] = []
    caption = caption or ""

    if caption:
        # Measured against the same preview the simulator draws, so the two
        # can't disagree about where the fold falls.
        first = _split_sentences(caption.split("\n", 1)[0])[0]
        preview = instagram_preview(caption)
        if len(first) > len(preview):
            issues.append(
                {
                    "level": "warn",
                    "message": (
                        f"첫 문장({len(first)}자)이 '더보기' 전에 보이는 부분({len(preview)}자) 안에 "
                        f"다 들어가지 않습니다(앞 {IG_FOLD}자·최대 {IG_FOLD_LINES}줄까지 보임). "
                        "훅이 잘린 채 노출되니 첫 문장을 짧게 끊으세요."
                    ),
                }
            )

    fixed_tags = list(hashtags or [])
    if len(fixed_tags) > IG_HASHTAG_MAX:
        issues.append(
            {
                "level": "fixed",
                "message": f"해시태그가 {len(fixed_tags)}개라 앞 {IG_HASHTAG_MAX}개만 남겼습니다.",
            }
        )
        fixed_tags = fixed_tags[:IG_HASHTAG_MAX]
    elif len(fixed_tags) < IG_HASHTAG_MIN:
        issues.append(
            {
                "level": "warn",
                "message": (
                    f"해시태그가 {len(fixed_tags)}개뿐입니다 "
                    f"({IG_HASHTAG_MIN}~{IG_HASHTAG_MAX}개 권장). 태그는 인스타의 주요 발견 경로입니다."
                ),
            }
        )

    return caption, fixed_tags, issues


def validate_shorts(script: dict) -> Tuple[dict, List[Dict]]:
    """Returns (script, issues). Shorts had only a simulator warning while X
    got automatic splitting. Captions are measured, not cut — a 20-character
    on-screen line is copy, and chopping it mid-word is worse than flagging
    it — but the one mechanical requirement, #shorts in the title, is added."""
    issues: List[Dict] = []
    script = dict(script or {})
    scenes = script.get("scenes") or []

    long_cuts = []
    if len(script.get("hook") or "") > SHORTS_CAPTION_MAX:
        long_cuts.append(f"훅({len(script['hook'])}자)")
    for i, scene in enumerate(scenes, start=1):
        caption = scene.get("caption") or ""
        if len(caption) > SHORTS_CAPTION_MAX:
            long_cuts.append(f"{i}번 컷({len(caption)}자)")
    if long_cuts:
        issues.append({
            "level": "warn",
            "message": (
                f"쇼츠 자막이 {SHORTS_CAPTION_MAX}자를 넘습니다: {', '.join(long_cuts)} — "
                "화면 우측 버튼·하단 캡션에 가려질 수 있으니 짧게 줄이세요."
            ),
        })

    title = (script.get("title") or "").strip()
    if title and "#shorts" not in title.lower():
        tags = [str(t).lower() for t in script.get("hashtags") or []]
        if "#shorts" not in tags:
            title = f"{title} #shorts"
            issues.append({"level": "fixed", "message": "쇼츠 제목에 #shorts가 없어 붙였습니다."})
    script["title"] = title
    if len(title) > SHORTS_TITLE_MAX:
        issues.append({
            "level": "warn",
            "message": f"쇼츠 제목이 {len(title)}자로 권장({SHORTS_TITLE_MAX}자)을 넘습니다 — 목록에서 잘려 보입니다.",
        })
    return script, issues


def validate_naver_tags(tags: List[str], fallback_keywords: List[str]) -> Tuple[List[str], List[Dict]]:
    """Returns (tags, issues) for the 발행-popup tag list.

    The writer's list used to go out unchecked, and a response it couldn't
    parse came back as [] — the post then went up with no tags at all and
    nothing said so. Tags are normalised ('#', no spaces, no duplicates),
    trimmed to the maximum, and topped up from the post's target keywords
    when the model returned too few.
    """
    issues: List[Dict] = []

    def norm(tag: str) -> str:
        body = re.sub(r"\s+", "", str(tag or "")).lstrip("#")
        return f"#{body}" if body else ""

    out: List[str] = []
    seen = set()
    for tag in tags or []:
        t = norm(tag)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)

    if len(out) > NAVER_TAG_MAX:
        issues.append({"level": "fixed", "message": f"발행 태그가 {len(out)}개라 앞 {NAVER_TAG_MAX}개만 남겼습니다."})
        out = out[:NAVER_TAG_MAX]

    if len(out) < NAVER_TAG_MIN:
        before = len(out)
        for kw in fallback_keywords or []:
            t = norm(kw)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
            if len(out) >= NAVER_TAG_MIN:
                break
        if len(out) > before:
            issues.append({
                "level": "fixed",
                "message": f"발행 태그가 {before}개뿐이라 이 글의 타깃·SEO 키워드로 {len(out) - before}개를 채웠습니다.",
            })
        if len(out) < NAVER_TAG_MIN:
            issues.append({
                "level": "warn",
                "message": f"발행 태그가 {len(out)}개뿐입니다({NAVER_TAG_MIN}~{NAVER_TAG_MAX}개 권장). 직접 추가하세요.",
            })
    return out, issues
