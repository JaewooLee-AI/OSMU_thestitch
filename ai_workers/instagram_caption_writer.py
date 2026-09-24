"""Instagram caption pipeline: same source material as the Naver draft
(memo + photo captions + Brand Kit voice), but a completely different
content shape.

Instagram's discovery signals are hashtags plus a scroll-stopping hook in the
caption's first ~125 characters, before the "more" truncation — not Naver's
keyword-density SEO. ai_workers/seo_optimizer.py deliberately does not run on
this output: a long, keyword-repeating paragraph is exactly wrong here.

Unlike OSMU_admin, this runs for **both** news-seeded and manual campaigns —
the News/Manual split disappeared when the two pipelines merged into
ai_workers/content_writer.py, and there was never a channel reason for news
posts to be Naver-only.
"""
from __future__ import annotations

import json
import re
from typing import List

from ai_workers.multi_llm_router import generate_text
from ai_workers.prompt_builder import brand_voice_blocks

CAPTION_SYSTEM_PROMPT_BASE = (
    "당신은 인스타그램 콘텐츠 마케터입니다. 인스타그램 캡션은 '더보기'로 접히는 첫 125자 안에 "
    "스크롤을 멈추게 하는 훅이 있어야 하고, 네이버 블로그처럼 길고 정보 나열식인 문단이 아니라 "
    "짧고 리듬감 있는 문장으로 씁니다. 첫 줄은 절대 브랜드 소개로 시작하지 말고, 독자가 "
    "자기 이야기라고 느낄 장면이나 질문으로 시작하세요.\n"
    "해시태그는 브랜드 태그, 카테고리 태그, 니치 태그를 섞어 8~15개 제안하세요.\n"
    "반드시 아래 JSON 형식으로만 응답하고 다른 설명은 포함하지 마세요:\n"
    '{"caption": "훅으로 시작하는 전체 캡션 본문", "hashtags": ["#태그1", "#태그2"]}'
)


def build_system_prompt(brand_kit: dict) -> str:
    """Reuses persona/tone/glossary but intentionally skips seo_keywords —
    Naver keyword targets don't belong in an Instagram caption."""
    return "\n\n".join([CAPTION_SYSTEM_PROMPT_BASE] + brand_voice_blocks(brand_kit))


def _parse_response(raw: str, fallback_text: str) -> dict:
    try:
        cleaned = re.sub(r"```json\s*|```\s*$", "", raw.strip())
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        caption = parsed.get("caption") or fallback_text
        hashtags = [h if str(h).startswith("#") else f"#{h}" for h in (parsed.get("hashtags") or [])]
        return {"caption": caption, "hashtags": hashtags}
    except Exception:
        return {"caption": fallback_text, "hashtags": []}


def write_instagram_caption(
    note: str, photo_captions: List[str], brand_kit: dict, vendor: str, facts: str = ""
) -> dict:
    """Returns {"caption": str, "hashtags": list[str]}. Best-effort — the
    caller must not let a failure here fail the whole run, since the Naver
    draft is the primary deliverable.

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
        note="instagram-caption",
    )
    return _parse_response(raw, note)
