"""사진 설명 — 네이버 편집기의 '사진 설명을 입력하세요' 칸에 들어갈 한 줄.

The blog is photo-heavy — the posts the marketer wrote by hand carried 8 to
18 photos against 600~1,500 characters, and their key facts ("약 4cm",
"머리에도 가슴에도") were lettered onto the images, where search cannot read
them. Naver's editor has a caption line under every photo, and the app left
it empty. A caption is post text: it gives each photo something searchable
without adding paragraphs to a post whose owner dislikes long-winded copy.

One call writes all captions from what the app already knows — the vision
description of each photo and the paragraph around its [IMAGE] tag — so a
caption names what is in the photo *in this post's terms* ("색동 치마를 입은
노리개키링, 약 10cm") rather than generically ("인형 세 개").
"""
from __future__ import annotations

import json
import re
from typing import Dict, List

from ai_workers.guardrail import apply_blacklist_dictionary
from ai_workers.multi_llm_router import generate_text
from ai_workers.photo_placement import IMAGE_TAG_RE

MAX_CHARS = 40

SYSTEM_PROMPT = (
    "당신은 네이버 블로그 편집자입니다. 글에 들어간 사진마다 사진 바로 아래에 붙을 **사진 설명 "
    "한 줄**을 쓰세요.\n\n"
    "규칙:\n"
    "- 15~30자. 문장이 아니라 짧은 설명구로 씁니다(마침표 없이).\n"
    "- 사진에 실제로 보이는 것을, 이 글에서 부르는 이름으로 씁니다(예: '색동 치마를 입은 노리개키링').\n"
    "- 크기·색상·쓰임처럼 글에 있는 정보 중 그 사진과 맞는 것을 하나 덧붙여도 좋습니다.\n"
    "- 글과 사진 설명 어디에도 없는 사실(수치·인증·가격)은 쓰지 않습니다. 과장 표현도 쓰지 않습니다.\n"
    "- 사진마다 다른 말로 씁니다. 같은 문구를 반복하지 마세요.\n\n"
    '반드시 아래 JSON만 출력하세요: {"captions": [{"photo": 1, "caption": "..."}]}'
)


def _context_around(content: str, path: str, width: int = 160) -> str:
    """Text right before and after this photo's tag."""
    for match in IMAGE_TAG_RE.finditer(content or ""):
        if match.group(1).strip() == path:
            before = content[max(0, match.start() - width):match.start()]
            after = content[match.end():match.end() + width]
            clean = lambda s: re.sub(r"\s+", " ", IMAGE_TAG_RE.sub(" ", s)).strip()  # noqa: E731
            return f"앞: {clean(before)} / 뒤: {clean(after)}"
    return ""


def ordered_photos(content: str, attached: List[str]) -> List[str]:
    """Photos in the order they appear in the post, untagged ones last."""
    tagged = [m.group(1).strip() for m in IMAGE_TAG_RE.finditer(content or "")]
    seen = list(dict.fromkeys(tagged))
    return seen + [p for p in attached or [] if p not in seen]


def write_captions(
    title: str, content: str, attached: List[str], vision: Dict[str, str], brand_kit: dict, vendor: str,
    existing: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """{rel_path: caption}. Keeps `existing` captions (the marketer may have
    edited them) and writes only the missing ones. Best-effort: returns what
    it has on any failure."""
    existing = {k: v for k, v in (existing or {}).items() if v and v.strip()}
    photos = ordered_photos(content, attached)
    missing = [p for p in photos if p not in existing]
    if not missing:
        return {p: existing[p] for p in photos if p in existing}

    lines = []
    for i, path in enumerate(missing, 1):
        lines.append(
            f"{i}. 사진 내용: {(vision or {}).get(path, '(분석 없음)')}\n   글에서의 위치: {_context_around(content, path)}"
        )
    body = IMAGE_TAG_RE.sub("", content or "")
    prompt = f"[제목] {title}\n[본문 앞부분]\n{body[:1200]}\n\n[사진 목록]\n" + "\n".join(lines)
    try:
        raw = generate_text(vendor=vendor, prompt=prompt, system=SYSTEM_PROMPT, max_tokens=1200, note="photo-captions")
        cleaned = re.sub(r"```json\s*|```\s*$", "", (raw or "").strip())
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        items = json.loads(match.group(0)).get("captions", []) if match else []
    except Exception as exc:  # noqa: BLE001 — captions never fail a run
        print(f"[photo_captions] skipped: {exc}")
        items = []

    written: Dict[str, str] = {}
    blacklist = brand_kit.get("blacklist_map") or {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("photo")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(missing):
            continue
        caption = " ".join((item.get("caption") or "").split()).strip("\"'“”").rstrip(".")
        if not caption or "[IMAGE" in caption:
            continue
        caption, _ = apply_blacklist_dictionary(caption, blacklist)
        written[missing[index]] = caption[:MAX_CHARS]

    merged = {**existing, **written}
    return {p: merged[p] for p in photos if p in merged}
