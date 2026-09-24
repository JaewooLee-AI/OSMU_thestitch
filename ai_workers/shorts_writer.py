"""Shorts / Reels script writer — the fourth channel shape.

OSMU_web shipped a ShortsView simulator but OSMU_admin never generated
anything to put in it, so the panel always rendered an empty 9:16 frame. This
fills that gap, which matters for 더스티치 specifically: their 2026 홍보 외주제작
plan budgets 쇼츠 10종 as a deliverable, and the reference channel in
company_info/sns_info.txt is an Instagram Reel.

Shorts writing is not "the blog post, shorter". It's a shot list with on-
screen text, where the constraint is that the right-hand button cluster and
the bottom caption bar of the player eat roughly the outer 25% of the frame —
so any text that carries meaning has to sit in the middle band. The simulator
(flet_app/simulators/shorts.py) draws those dead zones; this writer is told about them
so the copy is short enough to survive them.
"""
from __future__ import annotations

import json
import re
from typing import List

from ai_workers.multi_llm_router import generate_text
from ai_workers.prompt_builder import brand_voice_blocks

SHORTS_SYSTEM_PROMPT_BASE = (
    "당신은 숏폼(유튜브 쇼츠/인스타 릴스) 영상 기획자입니다. 15~30초 분량의 세로형(9:16) "
    "영상 구성안을 만듭니다.\n"
    "- 첫 1~2초 안에 시청자를 붙잡지 못하면 스와이프됩니다. 첫 컷의 자막은 질문이나 "
    "의외의 장면 묘사로 시작하세요.\n"
    "- 화면 우측(버튼)과 하단(캡션·프로필)은 UI에 가려집니다. 자막은 화면 중앙 밴드에 들어가야 "
    "하므로 한 컷당 자막은 공백 포함 20자 이내로 짧게 쓰세요.\n"
    "- 컷은 4~6개로 구성하고, 각 컷마다 '무엇을 찍는지'(촬영 지시)와 '화면에 뜨는 자막'을 "
    "분리해서 쓰세요.\n"
    "- 마지막 컷에는 자연스러운 마무리 또는 행동 유도를 넣습니다.\n"
    "- 제목(영상 설명란 첫 줄)은 40자 이내로 쓰고 #shorts 를 포함하세요.\n"
    "반드시 아래 JSON 형식으로만 응답하고 다른 설명은 포함하지 마세요:\n"
    '{"title": "영상 제목 #shorts", "hook": "첫 컷 자막", '
    '"scenes": [{"shot": "촬영 지시", "caption": "화면 자막"}], "hashtags": ["#태그1"]}'
)


def build_system_prompt(brand_kit: dict) -> str:
    return "\n\n".join([SHORTS_SYSTEM_PROMPT_BASE] + brand_voice_blocks(brand_kit))


def _parse_response(raw: str, fallback_text: str) -> dict:
    try:
        cleaned = re.sub(r"```json\s*|```\s*$", "", raw.strip())
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        scenes = []
        for scene in parsed.get("scenes") or []:
            if isinstance(scene, dict):
                scenes.append(
                    {"shot": str(scene.get("shot", "")).strip(), "caption": str(scene.get("caption", "")).strip()}
                )
        return {
            "title": (parsed.get("title") or fallback_text[:40]).strip(),
            "hook": (parsed.get("hook") or "").strip(),
            "scenes": scenes,
            "hashtags": [h if str(h).startswith("#") else f"#{h}" for h in (parsed.get("hashtags") or [])],
        }
    except Exception:
        return {"title": fallback_text[:40], "hook": "", "scenes": [], "hashtags": []}


def write_shorts_script(note: str, photo_captions: List[str], brand_kit: dict, vendor: str) -> dict:
    """Returns {"title", "hook", "scenes": [{shot, caption}], "hashtags"}.
    Best-effort, same as the other secondary channels."""
    context = "\n".join(f"- {c}" for c in photo_captions) if photo_captions else "(첨부된 사진 없음)"
    prompt = (
        f"[담당자가 작성한 메모]\n{note}\n\n"
        f"[촬영 소재로 쓸 수 있는 사진 설명]\n{context}"
    )
    raw = generate_text(
        vendor=vendor,
        prompt=prompt,
        system=build_system_prompt(brand_kit),
        max_tokens=900,
        note="shorts-script",
    )
    return _parse_response(raw, note)
