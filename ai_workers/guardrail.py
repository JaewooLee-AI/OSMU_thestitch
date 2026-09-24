"""Second-pass compliance reviewer, rebuilt for 더스티치's actual legal exposure.

OSMU_admin's guardrail audited against 의료법 (medical advertising law),
because that engine was built for a healthcare client. 더스티치 sells upcycled
hanbok goods and environmental education, so the law that actually bites here
is **환경성 표시·광고** — 「환경기술 및 환경산업 지원법」 제16조의10 and the
환경부 '환경성 표시·광고 관리제도에 관한 고시', plus 표시·광고의 공정화에 관한
법률. The classic failure mode isn't "cures cancer", it's greenwashing:
absolute claims ("100% 친환경"), unverifiable quantities ("탄소 3kg 절감"),
and borrowing the authority of certifications the company doesn't hold.

Three layers, same defense-in-depth shape as the original:

1. **Deterministic dictionary substitution** — admin-maintained
   "금기어 -> 치환어" pairs (Brand Kit) are swapped verbatim,
   case-insensitively. A known-bad term never survives, whatever the LLM does.
2. **LLM structured audit** — catches contextual/novel violations the fixed
   dictionary can't, and returns a score + explanation rather than silently
   rewriting, so the admin can see *why* something was flagged. The Brand
   Kit's core facts and glossary go in as the only permitted source of truth,
   so a price or certification count that contradicts them is a finding
   rather than something the audit has no way to evaluate.
3. **Deterministic certification-scope check** — a certification claimed for
   a collective subject ("저희 제품들은 … 인증을 받아"). The audit is told
   about this too, but it passed five consecutive drafts that did it, and
   미보유 인증 암시 is the single most likely 환경성 표시·광고 violation for an
   upcycling brand — so it gets a hard check, not just an instruction.

Only invoked when the Brand Kit guardrail toggle is on.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ai_workers.multi_llm_router import generate_text

AUDIT_SYSTEM_PROMPT = (
    "당신은 대한민국 「환경기술 및 환경산업 지원법」의 환경성 표시·광고 관리제도와 "
    "「표시·광고의 공정화에 관한 법률」을 기준으로 마케팅 문안을 검토하는 법무 검토관입니다. "
    "새활용(업사이클링) 제품과 친환경 교육서비스를 판매하는 사회적기업의 글을 검토합니다.\n\n"
    "다음을 중점적으로 찾아내세요:\n"
    "1) 포괄적·절대적 환경성 주장 — '100% 친환경', '완전 무해', '지구를 살리는', '무독성' 등 "
    "제품 전 과정을 입증하지 않고 쓰는 표현.\n"
    "2) 검증 불가능한 정량 효과 — 근거 자료 없이 제시한 탄소 절감량, 물 절약량, 폐기물 감축량.\n"
    "3) 보유하지 않은 인증·수상의 암시, 그리고 **보유한 인증의 범위를 넘겨 쓰는 것**. "
    "인증은 인증받은 품목에만 붙습니다. 일부 품목만 인증받았는데 '저희 제품들은', "
    "'모든 소품은'처럼 전체가 인증받은 것처럼 읽히면 위반입니다. 인증을 품질·신뢰의 "
    "일반적 보증('인증을 받아 더욱 믿을 수 있습니다')으로 확대하는 것도 위반입니다.\n"
    "4) 최상급·배타적 표현 — '국내 최초', '업계 최고', '유일한', '완벽한'.\n"
    "5) 소비자를 위축시키는 공포·죄책감 소구 — '쓰지 않으면 지구가 망합니다' 류.\n"
    "6) **[검증된 사실]과 어긋나는 수치** — 가격대, 수량, 연도, 실적, 인증 건수. "
    "[검증된 사실]에 없거나 그와 다른 숫자가 나오면 반드시 지적하고, replacement에서는 "
    "[검증된 사실]에 맞게 고치거나 숫자를 빼세요. 절대 새 숫자를 지어내지 마세요.\n\n"
    "제품의 색감·소재·쓰임새에 대한 사실 서술이나, 기부받은 한복을 재료로 쓴다는 "
    "설명 자체는 문제가 아닙니다. 과장하지 않은 표현까지 억지로 고치지 마세요.\n\n"
    "텍스트 중간에 `[IMAGE: 경로]` 형식의 태그나 `<<<`로 시작하는 구분자가 있다면 "
    "마크업이므로 phrase에 절대 포함하지 마세요.\n\n"
    "**issues에는 위반만 넣으세요.** '이런 정보를 더 넣으면 신뢰도가 올라갑니다' 같은 "
    "개선 제안은 위반이 아니므로 issues가 아니라 suggestions에 문자열로 넣으세요. "
    "빠뜨린 정보를 추가하라는 요구는 언제나 suggestions입니다 — 글에 없는 내용은 "
    "법령 위반이 아니라 선택의 문제입니다.\n\n"
    "issues의 각 항목은 반드시 {\"phrase\": ..., \"note\": ..., \"replacement\": ...} "
    "객체로 작성하세요. phrase에는 본문에 실제로 등장하는, 문제가 된 표현 '하나만' 글자 그대로 "
    "옮겨 적으세요 — 본문에 없는 문구를 넣으면 안 되고, note에서 언급하는 대안·추천 표현을 "
    "phrase에 넣어서도 안 됩니다. 문제 표현이 여러 개면 항목을 여러 개로 나누세요.\n"
    "**전체 글을 다시 쓰지 마세요.** 대신 replacement에 phrase 자리에 그대로 들어갈 교정 "
    "표현을 적으면, 시스템이 본문의 phrase를 replacement로 바꿉니다. 그러니 phrase는 바꾼 뒤에도 "
    "문장이 자연스럽게 이어지도록 조사·어미까지 포함한 단위로 잡으세요(필요하면 문장 하나 "
    "전체). replacement에 문제 표현을 다시 넣지 말고, 표현을 빼야 한다면 빈 문자열을 쓰세요. "
    "strengths에는 인증 근거를 "
    "정확히 연결하는 등 이미 잘 지켜진 점을 1~3개 문자열로 짧게 적으세요(없으면 빈 배열).\n\n"
    "반드시 아래 JSON 형식으로만 응답하고 다른 설명은 포함하지 마세요:\n"
    '{"compliance_pass": true, "score": 90, "strengths": ["잘 지켜진 점 1"], '
    '"issues": [{"phrase": "본문에 실제로 등장하는 문제 표현", '
    '"note": "문제 설명", "replacement": "phrase 자리에 들어갈 교정 표현"}], '
    '"suggestions": ["위반은 아니지만 넣으면 좋을 내용"]}'
)


# Sentences claiming a certification for a collective subject. The audit
# prompt asks for this too, but an instruction is not a guarantee: all five of
# the first production batch passed while carrying "더봄봄의 소품들은 …
# 새활용제품인증을 받아 더욱 믿을 수 있습니다", which claims a four-product
# certification for the whole catalogue. Under 환경성 표시·광고 관리제도 that is
# the 미보유 인증 암시 case this guardrail exists for, so it gets a
# deterministic detector like the banned-term dictionary has.
#
# Brand-agnostic by construction: the specific product names come from the
# Brand Kit glossary, so nothing here is 더스티치-specific.
_CERT_RE = re.compile(r"[가-힣A-Za-z]*인증")
_COLLECTIVE_RE = re.compile(
    r"모든|모두|전\s?제품|전\s?품목|전\s?라인|제품들|소품들|상품들|굿즈들|아이템들|라인업"
)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")

CERT_SCOPE_NOTE = (
    "인증받은 품목이 아니라 제품 전체가 인증된 것처럼 읽힙니다. "
    "인증받은 품목명을 직접 쓰거나('행복인형과 스크런치는 …'), "
    "인증 언급을 그 문장에서 빼세요. 「환경기술 및 환경산업 지원법」 환경성 표시·광고 "
    "관리제도상 미보유 인증 암시에 해당할 수 있습니다."
)


def check_certification_scope(text: str, brand_kit: dict) -> List[dict]:
    """Sentences that attach a certification to a collective subject.

    Deliberately narrow — a false positive here fails the campaign and sends
    the marketer into a regeneration loop. Three conditions must all hold:

    * the sentence mentions a certification, and
    * it mentions a collective noun (모든 / 제품들 / 소품들 …) **before** the
      certification, and
    * it names no specific product.

    Word order carries the distinction that matters. Korean modifiers precede
    their head, so "새활용제품인증을 받은 제품들은 …" is restrictive — the
    collective is scoped *by* the certification and the claim is accurate.
    Reverse them and the collective becomes the subject — "저희 제품들은 …
    인증을 받아" claims the certification for the whole catalogue. Only the
    second order is flagged; on the first production batch that is exactly
    one sentence out of five posts, and it is the one that over-claims.
    """
    # Brand names are not products: "더봄봄의 소품들은 …" names the brand and
    # still says nothing about which items are certified.
    excluded = {
        (brand_kit.get("brand_name") or "").strip(),
        (brand_kit.get("sub_brand") or "").strip(),
    }
    product_names = [
        name for name in (brand_kit.get("terminology") or {}).keys()
        if len(name) >= 2 and name not in excluded
    ]
    findings = []
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        cert = _CERT_RE.search(sentence)
        collective = _COLLECTIVE_RE.search(sentence)
        if not cert or not collective or collective.start() > cert.start():
            continue
        # A glossary term inside the certification word itself ('새활용' in
        # '새활용제품인증') is not the sentence naming a product.
        outside_cert = sentence[: cert.start()] + sentence[cert.end():]
        if any(name in outside_cert for name in product_names):
            # "행복인형과 스크런치 등 인증 제품들은 …" carries its own scope.
            continue
        findings.append({"phrase": sentence, "note": CERT_SCOPE_NOTE})
    return findings


def remove_certification_overclaims(text: str, brand_kit: dict) -> Tuple[str, List[str]]:
    """Deletes the sentences check_certification_scope flags. For the short
    SNS channels only (Instagram, X, Shorts).

    The scope check only *detects*, by design — on a blog post the right fix
    is usually naming the certified items, which needs judgement. A caption
    is different: the over-claiming sentence is one line of brand filler
    ("대표 제품들은 … 인증을 받아 …") in a few hundred characters, the post
    reads fine without it, and leaving it flagged meant every such caption
    shipped as '미해결' unless the marketer rewrote it by hand.
    """
    removed = []
    for finding in check_certification_scope(text, brand_kit):
        sentence = finding["phrase"]
        if sentence and sentence in text:
            text = text.replace(sentence, "", 1)
            removed.append(sentence)
    if removed:
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, removed


_ACRONYM_RE = re.compile(r"[A-Z]{3,}")


def _certification_terms(brand_kit: dict) -> List[str]:
    """Words that make a hashtag a certification claim: '인증' itself, plus
    acronyms of certifying bodies named in the brand's certification facts
    (e.g. a testing institute), so '#KOTITI' counts as well as '#…인증'."""
    terms = {"인증"}
    for fact in brand_kit.get("core_facts") or []:
        fact = str(fact)
        if "인증" in fact:
            terms.update(_ACRONYM_RE.findall(fact))
    return sorted(terms)


def filter_certification_hashtags(
    hashtags: List[str], shipped_text: str, brand_kit: dict
) -> Tuple[List[str], List[str]]:
    """Drops certification hashtags from a post whose text makes no
    certification statement. Returns (kept, removed).

    Hashtags were never audited at all: a caption about a product that is
    not certified went out tagged '#새활용제품인증'. A tag is a claim with no
    sentence around it to scope it, so it is only kept when the shipped text
    itself carries a certification statement (which the scope check has
    already vetted).
    """
    terms = _certification_terms(brand_kit)
    body_mentions = any(t in (shipped_text or "") for t in terms)
    kept, removed = [], []
    for tag in hashtags or []:
        if not body_mentions and any(t in str(tag) for t in terms):
            removed.append(tag)
        else:
            kept.append(tag)
    return kept, removed


def certification_block(brand_kit: dict) -> List[str]:
    """What is certified, for the channels that don't get the full fact list.

    The blog draft sees every core fact; the Instagram/X/Shorts writers only
    got persona, tone and glossary. The glossary names the certification but
    not which items hold it, so a caption about an uncertified item wrote
    "대표 제품들은 … 인증을 받아" — the model knew the certification existed
    and had no way to know its scope.
    """
    facts = [str(f).strip() for f in (brand_kit.get("core_facts") or []) if "인증" in str(f)]
    if not facts:
        return []
    return [
        "[인증 사실 — 인증은 아래에 적힌 대상에만 해당합니다. 이 글의 제품이 목록에 없으면 인증을 "
        "언급하지 마세요. '제품들', '대표 제품', '모든 제품'처럼 범위를 넓혀 쓰지 말고, 인증 관련 "
        "해시태그도 달지 마세요]\n" + "\n".join(f"- {f}" for f in facts)
    ]


def _verified_facts_block(
    brand_kit: dict,
    notice_fields: Optional[dict] = None,
    product_fields: Optional[dict] = None,
) -> str:
    """The only numbers and claims the audit may treat as true.

    Without this the audit has no ground truth, so it cannot tell a correct
    price from an invented one — which is how three posts in one batch quoted
    '5,000원부터', '1만 원대부터' and '1만~3만 원대' for the same products.

    The persona belongs here too, and not including it cost a real sentence.
    It says the writer has taught 친환경 공예 for '10년 넘게'; core_facts says
    the company was founded in 2017. The audit read the second as ground truth
    for the first, concluded the company could only be in its ninth year, and
    struck an approved claim about the founder's own teaching career out of the
    post. The founder's experience predates the company — the two numbers were
    never about the same thing.

    notice_fields/product_fields belong here for the same reason, and their
    absence cost two more real sentences. The marketer filled in this post's
    price (1만원대) and lead time (2일 이내 발송) — exactly the facts
    ai_workers/factsheet.py exists to protect from being invented — and the
    audit, seeing numbers absent from *its* ground truth, treated them as
    unverified: it deleted '2일 이내 발송' outright and rewrote '1만원대' up to
    '5,000원부터 30,000원대까지' to match the brand's general price zone in
    core_facts. Rule 6 of the audit prompt ("[검증된 사실]에 없거나 그와 다른
    숫자가 나오면... 고치거나 숫자를 빼세요") was doing exactly what it says —
    the campaign's own numbers just weren't in the block it was checking
    against. They still need to outrank the brand-wide figures in core_facts
    when the two disagree: core_facts describes what the brand generally
    charges, this campaign's factsheet describes what this post promises, and
    a promise trumps a generalization.
    """
    facts = [str(f).strip() for f in (brand_kit.get("core_facts") or []) if str(f).strip()]
    glossary = [
        f"{term}: {desc}" for term, desc in (brand_kit.get("terminology") or {}).items() if desc
    ]
    persona = (brand_kit.get("persona") or "").strip()
    if not facts and not glossary and not persona and not notice_fields and not product_fields:
        return ""
    lines = ["\n\n[검증된 사실 — 아래에 없는 수치·인증·실적은 근거 없는 것으로 취급하세요]"]
    lines += [f"- {f}" for f in facts]
    lines += [f"- {g}" for g in glossary]
    if persona:
        lines += [
            "\n[브랜드가 승인한 화자 설정 — 여기 담긴 경력·연차는 회사 설립연도와 별개로 "
            "이미 검증된 사실입니다. 이 내용과 일치하는 서술은 과장으로 지적하지 마세요]",
            f"- {persona}",
        ]

    campaign_facts = []
    if notice_fields or product_fields:
        from ai_workers import factsheet

        for sheet, given in ((factsheet.NOTICE, notice_fields), (factsheet.PRODUCT, product_fields)):
            campaign_facts += [
                f"- {sheet.labels[k]}: {v}" for k, v in factsheet.clean(sheet, given).items()
            ]
    if campaign_facts:
        lines += [
            "\n[이 글만의 사실 — 담당자가 이 캠페인을 위해 직접 입력한 값입니다. "
            "위 회사 전반의 수치(core_facts)와 다르더라도 이쪽이 이 글에서는 맞는 값이니 "
            "고치거나 삭제하지 마세요. 반대로 이 목록에 없는 가격·기간·수량을 새로 "
            "지어내는 것은 여전히 위반입니다]",
        ] + campaign_facts
    return "\n".join(lines)


def _parse_issue(item) -> Tuple[Optional[str], str]:
    """Returns (phrase, display_text) for one issue entry.

    `phrase` is the single problem expression the model isolated — used to
    check, later, whether it's still in the text. A model that ignores the
    schema and returns a bare string is tolerated (phrase=None): grounding
    and later resolution can't be verified for it, so it defaults to "still
    open" rather than silently disappearing from the report.
    """
    if isinstance(item, dict):
        phrase = (item.get("phrase") or "").strip() or None
        note = (item.get("note") or "").strip()
        if phrase and note:
            return phrase, f"'{phrase}' — {note}"
        return phrase, (note or phrase or "")
    return None, str(item)


# 위반이 아니라 '더 넣으면 좋겠다'는 제안임을 드러내는 말. 아래 _looks_like_suggestion
# 참고 — 프롬프트가 suggestions 필드를 따로 두었지만 지시는 보증이 아니고, 실제로
# 새활용제품인증을 본문에 더 소개하라는 권고가 issues로 올라와 글 전체를 재생성
# 대상으로 만들었다.
#
# 활용형 전체가 아니라 어간으로 등록한다. 처음엔 "권장합니다"·"좋습니다"처럼
# 종결형 그대로 넣었는데, "…소구력이 높아질 것입니다"라는 phrase 없는 제안이
# "높이는 것을"과 활용형이 달라 하나도 안 걸려서 위반으로 승격된 채 발행 직전까지
# 갔다. 한국어는 어미가 계속 바뀌므로("높아집니다/높아질 것입니다/높일 수 있습니다")
# 종결형을 나열하는 접근은 매번 새 활용형에서 또 뚫린다. 어간만 보면 그 활용형
# 전체를 한 번에 잡는다.
_SUGGESTION_HINTS = (
    "권장", "추천", "좋습니다", "좋을 것", "좋겠습니다",
    "도움이 됩", "도움이 될", "효과적", "높아집", "높아질", "높이는 것", "높일 수",
    "향상됩", "향상될", "전달됩니다", "전달될",
    # "…명확히 한정하여 표기하면 소비자의 오인을 예방할 수 있습니다" — preventive
    # advice, not a cited violation. Without these it kept an Instagram caption
    # at '미해결' forever: phrase-less, so nothing could ever mark it resolved.
    "예방할 수", "예방됩", "방지할 수", "방지됩", "명확히 하면", "명확히 표기", "표기하면",
)

# 반대로 이 말들이 있으면 제안처럼 쓰여 있어도 위반 판정으로 둡니다. 인증 범위
# 문제는 "명확히 나열하여 오해를 방지해야 합니다"처럼 개선 요구의 문장 형태를
# 띠지만 환경성 표시·광고 위반 그 자체입니다.
_VIOLATION_HINTS = (
    "위반", "과장", "오해", "오인", "금지", "근거 없", "허위", "확대", "소구", "지양",
    # '세상에 하나뿐인'을 두고 "객관적·절대적 사실로 오인될 수 있는 배타적
    # 표현입니다. … 수정 권장합니다"라고 적은 지적이 '권장' 때문에 제안으로
    # 강등돼 그대로 발행될 뻔했다. 최상급·배타적 표현은 표시광고법 위반이고
    # 금기어 사전이 이미 '유일한'을 잡고 있는 바로 그 종류다.
    "배타적", "최상급", "절대적", "단정",
)


def _has_violation_vocabulary(text: str) -> bool:
    return any(hint in text.lower() for hint in _VIOLATION_HINTS)


def _looks_like_suggestion(text: str, has_phrase: bool = True) -> bool:
    """Is this finding a recommendation rather than a violation?

    Only used as a backstop for a model that ignored the `suggestions` field.
    Deliberately asymmetric: a finding is downgraded only when it reads as a
    recommendation *and* carries none of the violation vocabulary. Anything
    ambiguous stays an issue, because the cost of the two errors is not the
    same — a suggestion treated as a violation wastes a regeneration, while a
    violation treated as a suggestion ships a 환경성 표시·광고 문제 to a live
    blog under the company's name.

    `has_phrase=False` flips that priority. A 보자기 포장 draft was flagged
    with no `phrase` at all for "기존에 인증받은 4개 품목과 별개의 서비스임을
    명확히 하여 … 오인하지 않도록 안내하면 더욱 좋습니다" — preventive advice
    about a mix-up that could happen, not a citation of a sentence that
    already claims the certification. '오인' put it in the violation
    vocabulary, so it forced compliance_pass=False and a 'regenerate'
    verdict — for advice that, like every phrase-less finding, no
    regeneration could ever resolve, because there was nothing wrong in the
    text to begin with.

    A finding that actually names a violating phrase almost always sets
    `phrase`, since the whole point of citing one is to point at words that
    exist. One that doesn't is more often describing a risk to watch for than
    something already wrong, so for these the suggestion vocabulary is
    checked first and the violation vocabulary override is skipped —
    ambiguous phrase-less findings with neither still fall through to
    `_classify_issues`'s existing default (open, since nothing can verify
    otherwise).
    """
    lowered = text.lower()
    if not has_phrase:
        return any(hint in lowered for hint in _SUGGESTION_HINTS)
    if _has_violation_vocabulary(text):
        return False
    return any(hint in lowered for hint in _SUGGESTION_HINTS)


def _classify_issues(raw_issues: List, reviewed_text: str) -> Tuple[List[str], List[str], Dict[str, str]]:
    """Splits LLM-reported issues into grounded vs unverified, and records
    each issue's isolated phrase for later use (see `content_writer.py`
    stage 6b, which checks whether the phrase survived into the shipped
    text). A phrase that doesn't actually appear anywhere in the text the
    model just read is a hallucinated citation, not a real finding, and must
    not fail the campaign or alarm the marketer as if it were one.
    """
    lowered_text = reviewed_text.lower()
    grounded, unverified, suggestions = [], [], []
    phrase_map: Dict[str, str] = {}
    for item in raw_issues:
        phrase, text = _parse_issue(item)
        if not text:
            continue
        if _looks_like_suggestion(text, has_phrase=bool(phrase)):
            suggestions.append(text)
            continue
        if phrase:
            phrase_map[text] = phrase
        if phrase and phrase.lower() not in lowered_text:
            unverified.append(text)
        else:
            grounded.append(text)
    return grounded, unverified, phrase_map, suggestions


def apply_blacklist_dictionary(text: str, blacklist_map: dict) -> Tuple[str, List[dict]]:
    """Deterministic, case-insensitive find/replace. Returns (sanitized, hits).

    Longest term first, regardless of the order the admin entered rows: many
    Korean banned phrases are prefixes of a longer, more specific one
    ("국내 최초" inside "국내 최초로"), and Korean glues particles onto the end of
    a phrase. Matching short-first would rewrite "국내 최초로" into
    "국내에서도 보기 드문로" — the substitution succeeds and the sentence breaks.
    Sorting by length makes the specific entry win without the admin having to
    know to order the table by hand.
    """
    sanitized = text
    hits = []
    ordered = sorted((blacklist_map or {}).items(), key=lambda kv: len(kv[0] or ""), reverse=True)
    for forbidden, replacement in ordered:
        if not forbidden:
            continue
        pattern = re.compile(re.escape(forbidden), re.IGNORECASE)
        if pattern.search(sanitized):
            hits.append({"forbidden": forbidden, "replacement": replacement})
            sanitized = pattern.sub(replacement, sanitized)
    return sanitized, hits


def run_llm_audit(
    text: str,
    vendor: str,
    brand_kit: Optional[dict] = None,
    notice_fields: Optional[dict] = None,
    product_fields: Optional[dict] = None,
) -> Dict:
    """Structured compliance report.

    **Fails closed.** An audit that errors out or returns unparseable output
    used to report `compliance_pass=True, score=100` — and the most common
    cause was the audit's own output being cut off, which happens on exactly
    the long posts with the most claims to check. A failed audit now comes
    back as one open, phrase-less issue (`audit_failed=True`): the draft is
    kept untouched, nothing crashes, but nothing reads as '검수 통과' either.

    The model returns phrase -> replacement edits instead of reproducing the
    whole text: output shrinks from 'the entire post' to a few lines (which is
    what made truncation likely), and the parts of the post the audit did not
    object to can no longer be silently rephrased along the way.
    """
    try:
        raw = generate_text(
            vendor=vendor,
            prompt=text,
            system=AUDIT_SYSTEM_PROMPT + _verified_facts_block(brand_kit or {}, notice_fields, product_fields),
            max_tokens=2000,
            note="guardrail-audit",
        )
    except Exception as exc:  # noqa: BLE001
        return _failed_audit(text, f"검수 호출 실패: {exc}")

    try:
        cleaned = re.sub(r"```json\s*|```\s*$", "", raw.strip())
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return _failed_audit(text, "검수 응답에서 결과(JSON)를 찾지 못했습니다", raw)
        parsed = json.loads(match.group(0))
        issues = parsed.get("issues") or []
        grounded, unverified, phrase_map, misfiled = _classify_issues(issues, text)
        # 모델이 스스로 suggestions에 넣은 것도 그대로 믿지는 않습니다. 첫 5건
        # 테스트에서 '세상에 하나뿐인'을 두고 "객관적·절대적 사실로 오인될 수 있는
        # 배타적 표현"이라고 정확히 진단해 놓고는 그걸 제안 칸에 넣었습니다.
        # 표시광고법상 배타적 표현이고 금기어 사전이 이미 '유일한'을 잡고 있는
        # 바로 그 종류라, 제안으로 두면 그대로 발행됩니다. 위반 어휘가 있으면
        # 되돌립니다 — 이 방향의 오류가 더 비싸다는 원칙은 여기서도 같습니다.
        declared, promoted = [], []
        for item in parsed.get("suggestions") or []:
            # (`suggestion`, not `text`: reusing the name used to overwrite the
            # audited text, so a missing corrected_text fell back to the last
            # suggestion string instead of the post.)
            suggestion = str(item).strip()
            if not suggestion:
                continue
            (promoted if _has_violation_vocabulary(suggestion) else declared).append(suggestion)
        grounded += promoted

        # Only edits for findings that survived classification are applied —
        # a finding downgraded to a suggestion must not rewrite the post.
        replacements = _replacements(issues)
        live_phrases = [p for p in phrase_map.values() if p]
        corrected = _apply_edits(text, {p: replacements[p] for p in live_phrases if p in replacements})

        try:
            score = int(parsed.get("score"))
        except (TypeError, ValueError):
            score = None
        return {
            "compliance_pass": not grounded,
            "score": score,
            "strengths": parsed.get("strengths") or [],
            "grounded_issues": grounded,
            "unverified_issues": unverified,
            "suggestions": declared + misfiled,
            "issue_phrases": phrase_map,
            "corrected_text": corrected,
        }
    except Exception as exc:  # noqa: BLE001
        return _failed_audit(text, f"검수 결과를 해석하지 못했습니다 ({exc})", raw)


AUDIT_FAILED_ISSUE = (
    "⚠️ 컴플라이언스 검수를 완료하지 못했습니다 — {reason}. 이 글은 검수되지 않은 상태이니 "
    "워크벤치에서 수정 요청을 비워 둔 채 [✏️ 수정 반영]을 눌러 검수만 다시 실행하거나, "
    "초안을 다시 생성하세요."
)


def _failed_audit(text: str, reason: str, raw: str = "") -> Dict:
    print(f"[guardrail] audit failed: {reason}")
    return {
        "compliance_pass": False, "score": None, "strengths": [],
        # Phrase-less on purpose: content_writer's resolution check keeps a
        # phrase-less issue open, so no later rewrite can make it look fixed.
        "grounded_issues": [AUDIT_FAILED_ISSUE.format(reason=str(reason)[:300])],
        "unverified_issues": [], "suggestions": [], "issue_phrases": {},
        "corrected_text": text, "parse_error": raw, "audit_failed": True,
    }


# Markup a replacement must never touch: photo slots, and the delimiters the
# X/Shorts audits use to split one audited string back into tweets/scenes.
_PROTECTED_MARKUP = ("[IMAGE:", "<<<")


def _replacements(raw_issues: List) -> Dict[str, str]:
    """phrase -> replacement for every well-formed edit the audit proposed."""
    out: Dict[str, str] = {}
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        phrase = (item.get("phrase") or "").strip()
        replacement = item.get("replacement")
        if not phrase or replacement is None:
            continue
        out[phrase] = str(replacement).strip()
    return out


def _apply_edits(text: str, edits: Dict[str, str]) -> str:
    """Applies phrase -> replacement edits deterministically.

    Longest phrase first, for the same reason as apply_blacklist_dictionary:
    one cited phrase is often a substring of another. An edit whose phrase or
    replacement touches protected markup is skipped rather than risk losing a
    photo slot or misaligning a thread.
    """
    corrected = text
    for phrase in sorted(edits, key=len, reverse=True):
        replacement = edits[phrase]
        if any(m in phrase or m in replacement for m in _PROTECTED_MARKUP):
            continue
        if phrase == replacement:
            continue
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        if not pattern.search(corrected):
            continue
        corrected = pattern.sub(lambda _m: replacement, corrected)
    # A deletion leaves doubled spaces / a space before punctuation behind.
    corrected = re.sub(r"[ \t]{2,}", " ", corrected)
    corrected = re.sub(r"[ \t]+([.,!?])", r"\1", corrected)
    return corrected


def review_and_sanitize(
    text: str,
    brand_kit: dict,
    vendor: str,
    notice_fields: Optional[dict] = None,
    product_fields: Optional[dict] = None,
) -> Dict:
    dict_sanitized, dictionary_hits = apply_blacklist_dictionary(text, brand_kit.get("blacklist_map", {}))
    audit = run_llm_audit(dict_sanitized, vendor, brand_kit, notice_fields, product_fields)

    # Checked against the text the audit *returns*, not the text it read: if
    # corrected_text already narrowed the over-broad sentence, there is
    # nothing left to report.
    grounded = list(audit["grounded_issues"])
    phrase_map = dict(audit["issue_phrases"])
    cert_findings = check_certification_scope(audit["corrected_text"], brand_kit)
    for finding in cert_findings:
        display = f"'{finding['phrase']}' — {finding['note']}"
        if display not in grounded:
            grounded.append(display)
            phrase_map[display] = finding["phrase"]

    # A dictionary hit is already fixed by the time this returns, and an
    # unverified issue cites a phrase that was never in the text — neither is
    # evidence that the *delivered* text is unsafe, so neither vetoes
    # compliance_pass. Only a grounded, still-live issue does.
    return {
        "final_text": audit["corrected_text"],
        "compliance_pass": not grounded,
        "score": audit["score"],
        "strengths": audit["strengths"],
        "dictionary_hits": dictionary_hits,
        "llm_issues": grounded,
        "unverified_issues": audit["unverified_issues"],
        # 위반이 아니라 '넣으면 좋을 것' — 판정을 좌우하지 않고 참고로만 보여줍니다.
        "suggestions": audit.get("suggestions") or [],
        "issue_phrases": phrase_map,
        "cert_scope_issues": [f["phrase"] for f in cert_findings],
        "audit_failed": bool(audit.get("audit_failed")),
    }


def apply_guardrail_if_enabled(
    text: str,
    brand_kit: dict,
    vendor: str,
    notice_fields: Optional[dict] = None,
    product_fields: Optional[dict] = None,
) -> Dict:
    """Returns a report dict even when disabled, so callers always have a
    consistent shape to persist (compliance_pass=None means 'skipped')."""
    if not brand_kit.get("guardrail_enabled", True):
        return {
            "final_text": text, "compliance_pass": None, "score": None, "strengths": [],
            "dictionary_hits": [], "llm_issues": [], "unverified_issues": [], "suggestions": [],
            "issue_phrases": {}, "cert_scope_issues": [],
        }
    return review_and_sanitize(text, brand_kit, vendor, notice_fields, product_fields)
