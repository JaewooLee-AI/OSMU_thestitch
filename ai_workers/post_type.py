"""What kind of post this is — 제품, 교육, 행사, 공지, 이야기.

Content modes (content_mode.py) decide how hard a post chases search
keywords. They could not decide *which* keywords make sense, because nothing
in the pipeline knew what the post was. Every post was written as a product
post: the length instruction listed 크기·구성·쓰임새·주문 방법, the only style
sample was a 리본핀 판매 글, and the keyword pool — 14 of 17 entries 답례품
or 제작 — was shown to a teacher-training announcement with "pick 2~3 and
place each 2~4 times". The marketer reported rewriting 교육 프로그램 posts
several times to take 답례품 back out, and a one-line 추석 연휴 안내 came back
titled 「…휴무 및 답례품 제작 안내」 with 돌답례품 in its first sentence.

factsheet.py deliberately avoids a type flag (which facts a post needs is the
writer's call, so the sheets are just filled in or not). This is a different
question — what the post is *about* — and the sheets cannot answer it: a
강좌 모집 and a 연휴 안내 both fill the 공지 sheet. So the type is inferred
from the memo and the sheets by default ('자동'), and the marketer can pin it
when the guess is wrong. The guess is deterministic and shown in the report,
so a wrong one is visible rather than silent.

Each type carries:
  * the section outline the draft should follow (see body_format.py)
  * what a first-time reader needs, replacing the product-only list
  * a cap on keyword pressure — a 교육 post never gets 'placement', a 공지
    never gets keywords at all, whatever the mode says
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ai_workers import content_mode

AUTO = "auto"

TYPES: Dict[str, dict] = {
    "product": {
        "label": "제품·답례품",
        "icon": "🛍️",
        "sells": True,
        "hint_cap": content_mode.HINT_PLACEMENT,
        # Shorter than the modes' 1,500~2,000자 (2026-10, at the 대표's
        # request — they dislike long-winded copy). The extra length the
        # modes ask for is where padding crept in; a product post with
        # captions, a spec list and a searchable title carries its facts in
        # 1,200~1,800자. '내용 우선' keeps its own deeper target.
        "length_range": (1200, 1800),
        "sections": "어떤 제품인지(소재·디자인) → 크기·구성·쓰임새 → 이런 분께·이런 자리에 → 주문·문의",
        "reader_needs": (
            "무엇인지, 무엇으로 어떻게 만들었는지, 크기·구성·쓰임새, 어떤 사람·상황에 맞는지, "
            "받는 사람이 느낄 점, 주문·이용 방법"
        ),
    },
    "education": {
        "label": "교육·체험",
        "icon": "🎨",
        "sells": False,
        # Keyword-free, like '내용 우선', whatever the mode says. Measured
        # 2026-10: of 21 education keywords none was winnable — 업사이클링체험
        # 130 searches against 970 posts per search, ESG교육 647, 진로체험프로그램
        # 40 searches — while the brand's product keywords sit at 6~50. The
        # people who book a 출강 or a 진로체험 are teachers and 기관 담당자 who
        # find a provider through 학교장터·꿈길·referrals and then read the blog
        # to check its track record. An education post is a portfolio piece:
        # it should be specific about who, what and how it went, and keyword
        # pressure only costs it that.
        "hint_cap": content_mode.HINT_NONE,
        "keyword_free": True,
        "sections": (
            "모집 글이면 어떤 수업인지(무엇을 만들고 배우는지) → 이런 분께 맞아요 → 진행 방식·준비물 → "
            "일정·신청 / 후기 글이면 어떤 기관·대상과 한 수업인지 → 수업 진행과 참여자 모습 → "
            "완성한 결과물과 반응 → 이런 기관·대상에 맞는 수업(문의)"
        ),
        "reader_needs": (
            "무엇을 만들고 무엇을 배우는지, 누구와 한 수업인지(학년·기관 유형·인원), 진행 방식, "
            "준비물, 완성한 결과물, 참여자의 반응, 강사·운영 경험, 일정과 신청·문의 방법 — 단, 메모에 "
            "있는 것만 쓰고 시간·인원·참여자 발언은 지어내지 않습니다"
        ),
    },
    "event": {
        "label": "행사·전시·팝업",
        "icon": "🎪",
        "sells": False,
        "hint_cap": content_mode.HINT_RELEVANCE,
        "sections": "어떤 행사인지 → 볼거리·체험거리 → 일시·장소·찾아오는 길",
        "reader_needs": (
            "어떤 행사인지, 무엇을 보고 체험할 수 있는지, 누가 오면 좋은지, 일시·장소·찾아오는 길, "
            "참여 방법"
        ),
    },
    "notice": {
        "label": "공지·안내",
        "icon": "📣",
        "sells": False,
        "hint_cap": content_mode.HINT_NONE,
        "keyword_free": True,
        "sections": "",
        "reader_needs": "알려야 할 사실(언제·무엇이 바뀌는지)과 독자가 해야 할 일",
    },
    "story": {
        "label": "브랜드 이야기·뉴스",
        "icon": "📰",
        "sells": False,
        "hint_cap": content_mode.HINT_RELEVANCE,
        "sections": "무슨 일이 있었는지 → 우리가 어떻게 보는지(의미) → 앞으로의 이야기",
        "reader_needs": "무슨 일이 있었는지, 왜 의미가 있는지, 브랜드의 활동과 어떻게 이어지는지",
    },
}

ORDER: List[str] = [AUTO, "product", "education", "event", "notice", "story"]

# 자동 판단 단서. 순서가 곧 우선순위입니다 — '추석 연휴 체험 수업 안내'는
# 공지보다 교육 글이고, '팝업 휴무'는 행사보다 공지에 가깝지만 그런 경우는
# 담당자가 직접 고르면 됩니다.
_EDUCATION_RE = re.compile(r"교육|강의|강좌|수업|클래스|워크숍|워크샵|강사|자격증|수강|체험|프로그램|교안|방과후")
_EVENT_RE = re.compile(r"팝업|전시|행사|마켓|페스타|페스티벌|박람회|축제|부스|플리마켓")
_NOTICE_RE = re.compile(r"휴무|연휴|휴가|휴업|공지|배송\s?지연|운영\s?시간|임시|휴점|택배\s?마감")


def guess(memo: str = "", notice_fields: Optional[dict] = None,
          product_fields: Optional[dict] = None, is_news: bool = False) -> str:
    """Best guess from what the marketer already entered. Deterministic."""
    from ai_workers import factsheet

    if factsheet.filled(factsheet.PRODUCT, product_fields):
        return "product"
    notice_text = " ".join((notice_fields or {}).values()) if notice_fields else ""
    text = f"{memo or ''} {notice_text}"
    if _EDUCATION_RE.search(text):
        return "education"
    if _EVENT_RE.search(text):
        return "event"
    if _NOTICE_RE.search(text):
        return "notice"
    if is_news:
        return "story"
    return "product"


def resolve(stored: Optional[str], memo: str = "", notice_fields: Optional[dict] = None,
            product_fields: Optional[dict] = None, is_news: bool = False) -> dict:
    """Profile for a campaign: the pinned type, or the guess when '자동'/empty."""
    key = (stored or "").strip()
    auto = key not in TYPES
    if auto:
        key = guess(memo, notice_fields, product_fields, is_news)
    return {"key": key, "auto": auto, **TYPES[key]}


_HINT_RANK = {content_mode.HINT_NONE: 0, content_mode.HINT_RELEVANCE: 1, content_mode.HINT_PLACEMENT: 2}


def apply_to_mode(mode: dict, ptype: dict) -> dict:
    """The content mode, narrowed by what this kind of post can carry.

    Only ever weakens: a mode the marketer chose is never made more
    aggressive by the type. A 공지 is measured but never rewritten for
    keywords and keeps no length target — there is little to say, and
    padding it is how 연휴 안내 posts filled up with brand history.
    """
    out = dict(mode)
    hint = mode.get("hint", content_mode.HINT_RELEVANCE)
    cap = ptype.get("hint_cap", content_mode.HINT_PLACEMENT)
    if _HINT_RANK.get(hint, 1) > _HINT_RANK.get(cap, 2):
        out["hint"] = cap
    if ptype.get("keyword_free"):
        # Behaves as '내용 우선' without the marketer having to remember to
        # pick it: measured and reported, never rewritten for keywords.
        out["enforce_density"] = False
        out["rewrite_title"] = False
    if ptype.get("length_range") and mode.get("length_range") and mode.get("key") != "rich":
        out["length_range"] = ptype["length_range"]
    if ptype["key"] == "notice":
        out["length_range"] = None
    return out


def title_keyword_rule(mode: dict, ptype: dict) -> str:
    """'required' | 'optional' | 'none' — how the title instruction treats keywords.

    The title instruction used to demand "최소 1개" from the whole pool in
    every mode, including '내용 우선', which promises not to show the pool at
    all. A title is the post's whole promise, so it is the last place an
    off-topic keyword belongs.
    """
    hint = mode.get("hint", content_mode.HINT_RELEVANCE)
    if hint == content_mode.HINT_NONE:
        return "none"
    if ptype.get("sells") and hint == content_mode.HINT_PLACEMENT:
        return "required"
    return "optional"


def structure_block(ptype: dict) -> List[str]:
    """Post-type section outline and focus, for the draft's system prompt."""
    label = ptype["label"]
    if ptype["key"] == "notice":
        lines = [
            f"[글 유형: {label}] 알릴 사실을 첫 문단에 바로 쓰세요. 짧은 공지라 소제목은 "
            "쓰지 않아도 됩니다. 날짜·변경 사항처럼 나열되는 정보는 '• '로 시작하는 줄로 "
            "정리하세요. 제품 소개나 판매·주문 제작 이야기를 덧붙이지 마세요."
        ]
    else:
        lines = [
            f"[글 유형: {label}] 소제목 흐름 예시: {ptype['sections']}. "
            "자료에 없는 구획은 빼고, 소재에 맞게 소제목 문구를 새로 지으세요."
        ]
        if not ptype.get("sells"):
            lines.append(
                f"이 글은 {label} 글입니다. 제품 판매·주문 제작·답례품 홍보로 넘어가지 마세요. "
                "[회사 핵심 팩트] 중에서도 이 글의 소재와 같은 분야의 팩트만 쓰고, 제품 가격대나 "
                "납품 실적처럼 판매에 관한 팩트는 쓰지 마세요."
            )
    return ["\n".join(lines)]


def label_of(key: Optional[str]) -> str:
    if key == AUTO or key not in TYPES:
        return "🔎 자동 판단"
    return f"{TYPES[key]['icon']} {TYPES[key]['label']}"
