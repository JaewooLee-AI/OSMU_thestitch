"""긴 문장만 골라 짧게 나누기 — '장황하다'는 지시가 다음 글에서 사라지던 문제.

The tone guide has said "한 문장은 40자 안팎" since the first customer report,
and the marketer kept asking for "다정하게, 문장은 짧게" in revisions. Each
revision fixed that one post; the next draft came back long-winded again,
because an instruction is not a check. Measured over the posts in this app,
average sentence length ran 43~61자 and every post carried two to eight
sentences over 60자.

So this is the same verify-then-fix shape the keyword density and the
proofreader use: measure deterministically, and only when sentences are over
the limit ask the model to rewrite *those sentences*, returned as
before -> after pairs and applied as targeted replacements. Nothing else in
the post moves, and every pair is checked before it lands:

* the numbers in the sentence survive (a date or price must not be "tidied"),
* brand terms and SEO keywords survive (no silent density loss),
* the result is actually shorter per sentence than what it replaces.

Best-effort: any failure leaves the text as it was.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

from ai_workers.body_format import BULLET_MARK, heading_text
from ai_workers.multi_llm_router import generate_text

# Sentences longer than this are split. The tone guide asks for ~40자; 60 is
# where a sentence is clearly long rather than a little over, so short-ish
# sentences the model wrote naturally are left alone.
LONG_SENTENCE_CHARS = 60

# One call handles this many sentences at most — the longest first. Was 15;
# a 1,700자 교육 draft came back with 24 sentences over the limit and kept 9.
MAX_SENTENCES_PER_PASS = 30

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
_NUMBER_RE = re.compile(r"\d[\d,.:~]*")

SYSTEM_PROMPT = (
    "당신은 네이버 블로그 문장 편집자입니다. 아래 [긴 문장] 각각을 같은 뜻의 **짧은 문장 "
    "2~3개**로 나눠 다시 쓰세요.\n\n"
    "규칙:\n"
    f"- 나눈 문장 하나는 {LONG_SENTENCE_CHARS - 20}자 안팎으로 씁니다.\n"
    "- 뜻·사실·숫자·날짜·가격·고유명사는 그대로 둡니다. 새 내용을 덧붙이지 않습니다.\n"
    "- 군더더기 수식어(정성스럽게, 한껏, 무척, 참 등)가 겹치면 덜어냅니다.\n"
    "- 말투는 원문처럼 다정한 존댓말을 유지합니다.\n"
    "- before에는 [긴 문장]을 글자 그대로 옮기고, after에는 나눈 문장들을 이어서 씁니다.\n\n"
    "반드시 아래 JSON 형식으로만 응답하세요:\n"
    '{"rewrites": [{"before": "원래 긴 문장", "after": "짧은 문장. 짧은 문장."}]}'
)


def _candidate_sentences(text: str) -> List[str]:
    """Body sentences, skipping headings, list items and photo tags."""
    out = []
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if (
            not stripped
            or heading_text(stripped) is not None
            or stripped.startswith(BULLET_MARK)
            or stripped.startswith("[IMAGE")
            or stripped.startswith("[원문 기사 출처")
        ):
            continue
        out += [s.strip() for s in _SENTENCE_RE.findall(stripped) if s.strip()]
    return out


def long_sentences(text: str, limit: int = LONG_SENTENCE_CHARS) -> List[str]:
    return [s for s in _candidate_sentences(text) if len(s) > limit]


def stats(text: str, limit: int = LONG_SENTENCE_CHARS) -> Dict:
    sentences = _candidate_sentences(text)
    lengths = [len(s) for s in sentences]
    return {
        "sentences": len(sentences),
        "avg": round(sum(lengths) / len(lengths)) if lengths else 0,
        "long": sum(1 for n in lengths if n > limit),
        "limit": limit,
    }


def _numbers(text: str) -> List[str]:
    # Trailing separators belong to the sentence, not the number: '13:00,'
    # and '13:00.' are the same time once the sentence is split.
    return sorted(n.rstrip(",.:~") for n in _NUMBER_RE.findall(text))


def _valid(before: str, after: str, protected: List[str]) -> Tuple[bool, str]:
    if not after or after == before:
        return False, "변경 없음"
    if "[IMAGE" in after:
        return False, "사진 태그 혼입"
    if _numbers(before) != _numbers(after):
        return False, "숫자 변경"
    for term in protected:
        if term and before.count(term) > after.count(term):
            return False, f"'{term}' 누락"
    pieces = [s.strip() for s in _SENTENCE_RE.findall(after) if s.strip()]
    if not pieces or max(len(p) for p in pieces) >= len(before):
        return False, "짧아지지 않음"
    # A split that drops most of the content is a summary, not a split.
    if len(after.replace(" ", "")) < len(before.replace(" ", "")) * 0.6:
        return False, "내용 축약"
    return True, ""


def shorten(text: str, vendor: str, protected: List[str] | None = None) -> Tuple[str, Dict]:
    """Returns (text, report). Splits only sentences over the limit."""
    protected = [t for t in (protected or []) if t]
    before_stats = stats(text)
    targets = sorted(set(long_sentences(text)), key=len, reverse=True)[:MAX_SENTENCES_PER_PASS]
    report = {"before": before_stats, "applied": [], "rejected": []}
    if not targets:
        report["after"] = before_stats
        return text, report

    prompt = "[긴 문장]\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(targets))
    try:
        raw = generate_text(
            vendor=vendor, prompt=prompt, system=SYSTEM_PROMPT,
            max_tokens=max(1200, sum(len(s) for s in targets) * 2), note="sentence-split",
        )
        cleaned = re.sub(r"```json\s*|```\s*$", "", (raw or "").strip())
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        rewrites = json.loads(match.group(0)).get("rewrites", []) if match else []
    except Exception as exc:  # noqa: BLE001 — a style pass never fails a run
        print(f"[sentence_length] skipped: {exc}")
        report["after"] = before_stats
        report["error"] = str(exc)
        return text, report

    result = text
    for item in rewrites:
        if not isinstance(item, dict):
            continue
        before = re.sub(r"^\d+\.\s*", "", (item.get("before") or "").strip())
        after = " ".join((item.get("after") or "").split())
        if before not in result:
            report["rejected"].append({"before": before, "after": after, "reason": "본문에 없는 문장"})
            continue
        ok, reason = _valid(before, after, protected)
        if not ok:
            report["rejected"].append({"before": before, "after": after, "reason": reason})
            continue
        result = result.replace(before, after, 1)
        report["applied"].append({"before": before, "after": after})

    report["after"] = stats(result)
    return result, report
