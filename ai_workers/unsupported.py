"""본문에 있지만 제공된 자료 어디에도 없는 숫자와 인용 — 지어낸 사실 찾기.

The prompts forbid inventing facts, and the compliance audit checks numbers
against the brand's verified facts. Neither caught this, from an education
review written from a four-sentence memo (2026-10): "소요 시간: 2교시 블록타임
(총 90분)" and two student quotes in quotation marks ("할머니 베갯맡에 꼭
놓아드릴 거예요") — none of it in the memo. A review is read by the school
it describes; a duration the class didn't take or words a student didn't say
are worse than leaving them out.

Deterministic and report-only, like factsheet.coverage: it compares the body
with everything the writer was given (memo, article, notice/product sheets,
core facts, glossary, photo captions) and lists what has no source. It does
not delete — a number can be legitimately derived, and the marketer may know
it is right — it points the marketer at what to verify.
"""
from __future__ import annotations

import re
from typing import Dict, List

# A number with the units that make it a claim: duration, headcount, count,
# price, size, date parts.
_NUMBER_RE = re.compile(
    r"(\d[\d,.]*)\s*(분|시간|명|회|번|원|만\s*원|천\s*원|cm|mm|센티|개|장|일|주|개월|년|월|교시|%|세|살|기)"
)
_QUOTE_RE = re.compile(r"[\"“]([^\"”\n]{6,})[\"”]")
_IMAGE_RE = re.compile(r"\[IMAGE:[^\]]*\]")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣]+")


def _norm(text: str) -> str:
    return _NON_WORD_RE.sub("", (text or "").lower())


def find(body: str, sources: List[str]) -> Dict[str, List[str]]:
    source = " ".join(s for s in sources if s)
    source_compact = re.sub(r"[\s,]", "", source)
    source_norm = _norm(source)
    text = _IMAGE_RE.sub(" ", body or "")

    numbers: List[str] = []
    for match in _NUMBER_RE.finditer(text):
        num = match.group(1).replace(",", "").rstrip(".")
        unit = re.sub(r"\s", "", match.group(2))
        if f"{num}{unit}" in source_compact:
            continue
        # A bare multi-digit number found in the sources is supported even if
        # the unit was phrased differently ('1,000회' vs '1,000번').
        if len(num) >= 2 and num in source_compact:
            continue
        label = match.group(0).strip()
        if label not in numbers:
            numbers.append(label)

    quotes: List[str] = []
    for match in _QUOTE_RE.finditer(text):
        said = _norm(match.group(1))
        if len(said) >= 6 and said[:12] not in source_norm:
            quotes.append(match.group(1).strip())
    return {"numbers": numbers, "quotes": quotes}


def sources_for(campaign: dict, brand_kit: dict, captions: Dict[str, str] | None = None,
                article_text: str = "") -> List[str]:
    """Everything the writer was given for this post."""
    out = [campaign.get("memo") or "", campaign.get("title") or "", article_text]
    for key in ("notice_fields", "product_fields"):
        out += list((campaign.get(key) or {}).values())
    out += list(brand_kit.get("core_facts") or [])
    out += [f"{k} {v}" for k, v in (brand_kit.get("terminology") or {}).items()]
    out += list((captions or {}).values())
    return out
