"""X (Twitter) thread pipeline: same source material as the Naver draft, but
a third distinct content shape and discovery model.

X's discoverability is keyword search plus a scroll-stopping first tweet. It
is neither Naver's keyword-density SEO (seo_optimizer.py doesn't run here)
nor Instagram's hashtag stacking — X's ranking treats three or more hashtags
as spammy and suppresses reach, so this asks for far fewer than the Instagram
writer does. Content is a numbered thread of short tweets, not one long
caption or body.
"""
from __future__ import annotations

import json
import re
from typing import List

from ai_workers.multi_llm_router import generate_text
from ai_workers.prompt_builder import brand_voice_blocks

# X weighs each Korean character as 2 against its 280 budget, so the working
# limit for Korean text is ~130 characters (sns_validator.TWEET_SOFT_MAX = 260
# weighted). This used to say 240, which is a Latin-text number — the model
# wrote to it and X refused the result.
TWEET_MAX_CHARS = 130  # Korean characters; spaces/ASCII count half

THREAD_SYSTEM_PROMPT_BASE = (
    "당신은 X(트위터) 콘텐츠 마케터입니다. X는 인스타그램과 정반대로 해시태그를 1~2개만 써야 "
    "도달이 잘 되고, 3개 이상이면 스팸으로 인식되어 노출이 줄어듭니다. 대신 검색 노출을 위해 "
    "핵심 키워드를 문장 안에 자연스럽게 녹여 쓰는 것이 훨씬 중요합니다. "
    "첫 트윗은 타임라인 스크롤을 멈추게 하는 훅 한 문장으로 시작하고, "
    f"각 트윗은 **한글 기준 {TWEET_MAX_CHARS}자**를 넘지 않게 짧고 리듬감 있게 끊어 스레드로 구성하세요 "
    "(X는 한글 1자를 2자로 계산해 280자 한도를 적용합니다). "
    "트윗은 보통 2~5개, 마지막 트윗에는 자연스러운 마무리나 행동 유도 문구를 넣으세요.\n"
    "반드시 아래 JSON 형식으로만 응답하고 다른 설명은 포함하지 마세요:\n"
    '{"tweets": ["첫 번째 트윗(훅)", "두 번째 트윗"], "hashtags": ["#태그1", "#태그2"]}'
)


def build_system_prompt(brand_kit: dict) -> str:
    """Persona/tone/glossary only — Naver keyword-density targets don't map
    onto a short-form thread."""
    return "\n\n".join([THREAD_SYSTEM_PROMPT_BASE] + brand_voice_blocks(brand_kit))


def _parse_response(raw: str, fallback_text: str) -> dict:
    try:
        cleaned = re.sub(r"```json\s*|```\s*$", "", raw.strip())
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        tweets = [str(t).strip() for t in (parsed.get("tweets") or []) if str(t).strip()]
        if not tweets:
            tweets = [fallback_text]
        hashtags = [h if str(h).startswith("#") else f"#{h}" for h in (parsed.get("hashtags") or [])]
        return {"tweets": tweets, "hashtags": hashtags}
    except Exception:
        return {"tweets": [fallback_text], "hashtags": []}


def write_x_thread(
    note: str, photo_captions: List[str], brand_kit: dict, vendor: str, facts: str = ""
) -> dict:
    """Returns {"tweets": list[str], "hashtags": list[str]}. Best-effort.

    `facts` is the confirmed-facts block and the finished (already audited)
    blog body — see content_writer._sns_facts. Without it this writer saw only
    the memo, so a date, price or article figure that lived in the notice
    sheet or the news source never reached the SNS copy.
    """
    context = "\n".join(f"- {c}" for c in photo_captions) if photo_captions else "(첨부된 사진 없음)"
    prompt = f"[담당자가 작성한 메모]\n{note}\n\n[첨부 사진 설명]\n{context}"
    if facts:
        prompt += "\n\n" + facts
    raw = generate_text(
        vendor=vendor,
        prompt=prompt,
        system=build_system_prompt(brand_kit),
        max_tokens=800,
        note="x-thread",
    )
    return _parse_response(raw, note)
