"""Turns raw keyword metrics into a brand-specific SEO keyword proposal.

`keyword_research` answers two objective questions — how many people search
a term, how many posts already compete for it — and both are brand-agnostic.
What it cannot answer is whether *this* company has anything honest to say
about the term. Left to the numbers alone, a sweep seeded with '한복,
업사이클링, 리폼' returns `자갈` at rank 8 with a perfectly good score,
because Naver's related-keyword data is drawn from ad co-occurrence rather
than topical meaning. No threshold removes that; it needs a reader.

The brand kit already holds everything a reader would need (industry, core
facts, persona), so the judgment runs there: candidates plus brand kit go to
the configured model, which drops the irrelevant ones and files the rest by
search intent. The intent groups are fixed and deliberately generic — they
describe why someone typed the query, not what the company sells, so they
hold for the next tenant of this app without a code change.

Nothing here writes to the brand kit. `propose()` returns a proposal; the
settings screen renders it as a diff and the human applies it.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ai_workers import keyword_research
from ai_workers.multi_llm_router import generate_text, get_configured_vendor
from core import repo

# Ordered by how directly the traffic converts, which is also the order the
# proposal is rendered in. `exclude` is not a group — it's the absence of one.
INTENT_GROUPS = {
    "purchase": "구매 직결",
    "prospect": "잠재 고객",
    "division": "사업 부문",
    "identity": "브랜드 정체성",
}

# Above this, the results page belongs to established publishers and a new
# post will not surface no matter how well it is written. Applied after the
# model has judged relevance so that the exclusion reason stays specific:
# 'irrelevant' and 'unwinnable' call for different follow-up.
MAX_DOCS_PER_SEARCH = 100

# Naver masks any per-device count under ten, which `_parse_count` records as
# 5 — so a keyword nobody searches on either device lands at exactly 10, not
# 0. Anything at or below that floor is unmeasurable rather than small, and
# ranking first for it wins nothing.
MIN_VIABLE_VOLUME = 20

# Both thresholds above are waived for the identity group, which exists to
# hold exactly the keywords they reject: a brand's own vocabulary is low
# volume and often crowded, and dropping it means the company disappears
# from search for the thing it actually does. Applying the guards to this
# group would leave it permanently empty. The cap is what keeps the waiver
# from becoming a loophole the model can route everything through.
IDENTITY_LIMIT = 3

# Naver's C-Rank scores a blog on how consistently it covers one subject, so
# a pool spread across five unrelated categories suppresses every post in it
# no matter how well each one is written — the blog never reads as an
# authority on anything. Keeping the two heaviest topics is what turns a
# scattered list into a focused one; the model only labels the topics, and
# the arithmetic below decides which survive, because "which subject does
# this brand have the most demand in" is a sum, not a judgment.
TOPIC_LIMIT = 2

SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 전략가입니다. 브랜드 정보와 키워드 후보 목록을 받아, "
    "각 키워드가 이 브랜드에게 쓸모 있는지 판정하고 검색 의도와 주제로 분류합니다.\n\n"
    "판정 기준:\n"
    "- **검색한 사람이 원하는 것을 이 브랜드가 줄 수 있어야 합니다.** 예를 들어 대여 서비스를 "
    "찾는 검색어인데 브랜드가 대여업을 하지 않는다면, 소재나 업종이 비슷해 보여도 제외하세요. "
    "그 사람은 브랜드가 팔지 않는 것을 찾고 있으므로 유입되어도 아무 의미가 없습니다.\n"
    "- 이 브랜드가 그 키워드로 **정직하게** 글 한 편을 쓸 수 있어야 합니다. "
    "브랜드가 실제로 하지 않는 일에 대한 키워드는 검색량이 아무리 커도 제외하세요.\n"
    "- **검색 의도가 여러 갈래로 갈리는 광범위한 일반어는 제외하세요.** 예를 들어 어떤 분야의 "
    "이름 자체(업계 용어 한 단어)는 그 분야의 취업·뉴스·학습 정보를 찾는 사람이 대부분이라 "
    "브랜드 고객과 연결되지 않습니다.\n"
    "- 지금 당장 제품을 사려는 검색이 아니어도, 그 사람이 언젠가 이 브랜드의 고객이나 "
    "공급자가 될 수 있다면 'prospect'로 채택하세요.\n"
    "- 검색량이 적어도 브랜드가 무엇을 하는 회사인지 드러내는 말이면 'identity'로 남기세요.\n\n"
    "그룹(검색 의도):\n"
    "- purchase: 제품·서비스를 사려는 검색\n"
    "- prospect: 아직 구매 의사는 없지만 브랜드와 접점이 되는 검색\n"
    "- division: 본 제품과 별개인 사업 부문(교육·B2B 등)에 해당하는 검색\n"
    "- identity: 검색량은 적지만 브랜드 정체성상 지켜야 하는 말\n\n"
    "topic(주제): 그 키워드가 속한 **상품·서비스 카테고리**를 2~6글자로 붙이세요. "
    "같은 카테고리의 키워드에는 반드시 완전히 똑같은 문자열을 쓰세요 "
    "(예: '돌답례품'과 '결혼답례품'은 둘 다 '답례품'). "
    "검색 의도(group)가 아니라 무엇에 관한 검색인지로 묶어야 합니다.\n\n"
    "반드시 아래 JSON만 출력하세요. 설명이나 코드펜스를 붙이지 마세요.\n"
    '{"keywords": [{"keyword": "...", "group": "purchase|prospect|division|identity", '
    '"topic": "...", "reason": "채택 이유 25자 이내"}], '
    '"excluded": [{"keyword": "...", "reason": "제외 이유 25자 이내"}]}'
)


def _brand_context() -> str:
    kit = repo.get_brand_kit()
    facts = kit.get("core_facts") or []
    return (
        f"브랜드명: {kit.get('brand_name') or '-'}\n"
        f"서브브랜드: {kit.get('sub_brand') or '-'}\n"
        f"업종: {kit.get('industry') or '-'}\n"
        f"핵심 사실:\n" + "\n".join(f"- {f}" for f in facts[:15])
    )


def _cached_int(keyword: str, metric: str, max_age_days: int) -> Optional[int]:
    hit = repo.get_cached_metric(keyword, metric, max_age_days)
    return int(hit) if hit is not None else None


def _parse(raw: str) -> dict:
    cleaned = re.sub(r"```json\s*|```\s*$", "", raw.strip())
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    return json.loads(match.group(0)) if match else {}


def propose(
    scored: List[dict], *, current: Optional[List[str]] = None, vendor: Optional[str] = None
) -> dict:
    """Group `keyword_research` output into an applyable proposal.

    `scored` is rank_keywords/score_candidates output (keyword, estimated_
    volume, documents, score). `current` is the brand kit's existing pool,
    which is judged alongside the candidates rather than preserved blindly —
    a keyword nobody searches has to be able to leave the list too.

    On a model or parse failure the whole thing is reported as unjudged
    rather than guessed at: silently proposing an unfiltered list is exactly
    the failure this module exists to prevent.
    """
    current = current or []
    by_keyword = {r["keyword"]: r for r in scored}
    for kw in current:
        if kw in by_keyword:
            continue
        # Incumbents have to face the same numbers as the candidates or they
        # survive on brand fit alone — which is how a pool of unsearched
        # terms perpetuates itself. Read from cache rather than calling out:
        # a prior 키워드 진단 has almost always measured these already, and a
        # curation step should never surprise the user with a metered request.
        by_keyword[kw] = {
            "keyword": kw,
            "estimated_volume": _cached_int(
                keyword_research.normalize(kw), "ad_volume", keyword_research.VOLUME_CACHE_DAYS
            ),
            "documents": _cached_int(kw, "blog_total", keyword_research.DOCUMENT_CACHE_DAYS) or 0,
            "score": 0,
        }

    metrics_lines = []
    for kw, row in by_keyword.items():
        volume = row.get("estimated_volume")
        docs = row.get("documents") or 0
        line = f"- {kw} | 월검색량 {volume if volume is not None else '미조회'} | 블로그문서 {docs:,}"
        if volume:
            line += f" | 문서/검색 {docs / volume:.0f}"
        metrics_lines.append(line)

    prompt = (
        f"{_brand_context()}\n\n"
        f"현재 SEO 키워드: {', '.join(current) if current else '(없음)'}\n\n"
        f"키워드 후보와 지표:\n" + "\n".join(metrics_lines)
    )

    try:
        raw = generate_text(
            vendor=vendor or get_configured_vendor(),
            prompt=prompt,
            system=SYSTEM_PROMPT,
            max_tokens=3000,
            note="keyword-curation",
        )
        parsed = _parse(raw)
    except Exception as exc:
        return {"error": str(exc), "groups": {}, "excluded": [], "current": current}

    groups: Dict[str, List[dict]] = {key: [] for key in INTENT_GROUPS}
    excluded: List[dict] = []

    for item in parsed.get("keywords", []):
        keyword = (item.get("keyword") or "").strip()
        row = by_keyword.get(keyword)
        if not keyword or row is None:
            continue  # the model invented a keyword that wasn't offered
        volume = row.get("estimated_volume") or 0
        docs = row.get("documents") or 0
        ratio = (docs / volume) if volume else None

        # Unrecognised group names fall to "prospect", not "identity" — the
        # latter waives the thresholds below, so a malformed response must
        # not be able to land there.
        group = item.get("group") if item.get("group") in INTENT_GROUPS else "prospect"

        # A relevance verdict doesn't override the arithmetic: the model has
        # no way to know that 370만 competing posts make a term unreachable.
        if group != "identity":
            if volume and volume <= MIN_VIABLE_VOLUME:
                excluded.append({**row, "reason": f"검색량 없음({volume}회 이하)"})
                continue
            if ratio is not None and ratio > MAX_DOCS_PER_SEARCH:
                excluded.append({**row, "reason": f"경쟁 과다(문서/검색 {ratio:.0f})"})
                continue

        groups[group].append(
            {
                **row,
                "reason": item.get("reason", ""),
                "ratio": ratio,
                "topic": (item.get("topic") or "기타").strip(),
            }
        )

    for item in parsed.get("excluded", []):
        keyword = (item.get("keyword") or "").strip()
        row = by_keyword.get(keyword)
        if row is not None:
            excluded.append({**row, "reason": item.get("reason") or "브랜드 무관"})

    # A keyword can be rejected twice — once by the numbers above, once by
    # the model — and listing it twice makes the revive checkboxes collide.
    excluded = list({r["keyword"]: r for r in excluded}.values())

    # Anything the model simply didn't mention. It used to vanish: the loops
    # above only ever read what came back, so a response covering 23 of 70
    # offered keywords produced a proposal that looked complete and had
    # quietly dropped 47. Measured on the first real sweep, the four lowest-
    # competition finds — 노리개키링 at 6.7 documents per search among them —
    # were all in the silent half.
    #
    # They go to `excluded`, which is the revivable list, rather than into a
    # group: the model not judging a keyword is not the same as judging it
    # good, and the UI already lets the marketer bring back anything there.
    judged = {r["keyword"] for rows in groups.values() for r in rows}
    judged.update(r["keyword"] for r in excluded)
    for keyword, row in by_keyword.items():
        if keyword in judged:
            continue
        excluded.append({**row, "reason": "AI가 판정하지 않음 — 직접 확인하세요"})

    # --- topic concentration (C-Rank) ---------------------------------------
    # Weight by search volume rather than keyword count: five niche terms in
    # one category shouldn't outrank two high-demand ones in another just by
    # being numerous. Identity is exempt — it is the brand's own vocabulary
    # and belongs in the pool whatever the dominant commercial topic is.
    weights: Dict[str, int] = {}
    for gkey, rows in groups.items():
        if gkey == "identity":
            continue
        for row in rows:
            weights[row["topic"]] = weights.get(row["topic"], 0) + (row.get("estimated_volume") or 0)

    focus = [t for t, _ in sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:TOPIC_LIMIT]]
    for gkey, rows in groups.items():
        if gkey == "identity":
            continue
        kept_rows = []
        for row in rows:
            if row["topic"] in focus:
                kept_rows.append(row)
            else:
                excluded.append(
                    {**row, "reason": f"주제 분산 — '{'·'.join(focus)}'에 집중"}
                )
        groups[gkey] = kept_rows

    for group in groups.values():
        group.sort(key=lambda r: r.get("score") or 0, reverse=True)

    # Identity is capped by volume rather than score, since score divides by
    # competition and these are the terms with no competition to speak of.
    if len(groups["identity"]) > IDENTITY_LIMIT:
        overflow = sorted(
            groups["identity"], key=lambda r: r.get("estimated_volume") or 0, reverse=True
        )
        groups["identity"] = overflow[:IDENTITY_LIMIT]
        excluded.extend(
            {**r, "reason": f"브랜드 정체성 키워드 {IDENTITY_LIMIT}개 초과"}
            for r in overflow[IDENTITY_LIMIT:]
        )

    accepted = [r["keyword"] for g in groups.values() for r in g]
    return {
        "groups": groups,
        "focus": focus,
        "excluded": excluded,
        "current": current,
        "accepted": accepted,
        "added": [k for k in accepted if k not in current],
        "removed": [k for k in current if k not in accepted],
        "kept": [k for k in accepted if k in current],
    }


SEED_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 전략가입니다. 브랜드 정보를 읽고, 연관키워드 발굴에 쓸 "
    "**씨앗 키워드 5개**를 제안하세요.\n\n"
    "씨앗 키워드는 그 자체로 쓸 키워드가 아니라, **연관검색어를 최대한 많이 끌어오기 위한 "
    "그물**입니다. 넓을수록 좋습니다.\n\n"
    "절대 규칙:\n"
    "- **수식어를 붙이지 마세요.** '친환경', '업사이클링', '수제', '맞춤', '프리미엄', "
    "'소량' 같은 형용사가 붙는 순간 검색량이 거의 0이 되어 연관검색어가 나오지 않습니다. "
    "'친환경 답례품'이 아니라 그냥 **'답례품'** 이라고 쓰세요.\n"
    "- **브랜드명·자체 용어·업계 전문용어를 쓰지 마세요.** 같은 이유로 결과가 비어버립니다.\n"
    "- 누구나 아는 **순수한 카테고리 이름 한 단어**를 쓰세요. 2~5글자가 적당합니다.\n"
    "- 브랜드가 다루는 서로 다른 카테고리를 5개 고르세요. "
    "한 카테고리에 몰면 그 축의 연관어만 나옵니다.\n\n"
    "좋은 예시의 형태: '답례품', '기념품', '굿즈제작', '공예키트', '한복'\n"
    "나쁜 예시의 형태: '친환경 답례품', '한복 업사이클링', '기업 ESG 행사'\n\n"
    '반드시 아래 JSON만 출력하세요: {"seeds": ["...", "...", "...", "...", "..."]}'
)


def suggest_seeds(vendor: Optional[str] = None) -> List[str]:
    """Seed keywords for a sweep, derived from the brand kit.

    Defaulting the seed box to the brand's own SEO pool was a dead end: that
    pool is exactly what a sweep exists to replace, and a term with no search
    volume has no related keywords to discover, so the sweep returned the
    seeds back and nothing else. The brand kit's industry and core facts
    describe the business in ordinary words, which is what Naver can match.
    """
    try:
        raw = generate_text(
            vendor=vendor or get_configured_vendor(),
            prompt=_brand_context(),
            system=SEED_SYSTEM_PROMPT,
            max_tokens=500,
            note="keyword-seeds",
        )
        seeds = _parse(raw).get("seeds", [])
    except Exception:
        return []
    return [s.strip() for s in seeds if isinstance(s, str) and s.strip()][:5]


# 그룹이 곧 그 유입의 사업적 가치에 대한 판정입니다. 담당자가 손으로 넣기
# 전까지의 출발값이고, 한 번 손대면 다시 덮지 않습니다.
GROUP_DEFAULT_WEIGHT = {
    "purchase": 2.0,    # 구매 직결 — 이 검색어로 들어오면 살 사람
    "division": 1.5,    # 사업 부문 — 문의로 이어지는 축
    "prospect": 1.0,    # 잠재 고객 — 지금 사지는 않음
    "identity": 1.0,    # 브랜드 정체성 — 아래에서 검색 타깃에서 뺍니다
}


def apply(keywords: List[str], proposal: Optional[dict] = None) -> None:
    """Write the approved list to the brand kit. Deliberately the only
    function here that mutates anything, and never called by `propose`.

    `proposal` carries the group each keyword landed in, and writing it is the
    point: without it the curation was a one-way loss of everything this
    module worked out. `identity` is the clearest case — the model correctly
    labels 더봄봄 and 한복 새활용 as brand vocabulary that draws 15 and 10
    searches a month, and then the old version of this function threw the
    label away and let them compete for titles like anything else.

    Weights are seeded per group but **never overwrite a weight the marketer
    already set**: the group is a starting guess from a model that has not
    seen a single order, and the human number is the one that knows what a
    기관 굿즈 enquiry is worth. Only keywords new to the pool get a default.

    Settings for keywords that left the pool are dropped, so a list that has
    been curated a few times doesn't accumulate weights for words nobody uses.
    """
    final = [k for k in dict.fromkeys(keywords) if k.strip()]
    fields = {"seo_keywords": final}

    if proposal:
        group_of = {
            row["keyword"]: gkey
            for gkey, rows in (proposal.get("groups") or {}).items()
            for row in rows
        }
        existing = repo.get_brand_kit()
        weights = dict(existing.get("keyword_weights") or {})
        non_targets = set(existing.get("non_target_keywords") or ())

        for keyword in final:
            gkey = group_of.get(keyword)
            if gkey == "identity":
                non_targets.add(keyword)
            if keyword not in weights and gkey in GROUP_DEFAULT_WEIGHT:
                weights[keyword] = GROUP_DEFAULT_WEIGHT[gkey]

        fields["keyword_weights"] = {k: v for k, v in weights.items() if k in final}
        fields["non_target_keywords"] = [k for k in final if k in non_targets]

    repo.save_brand_kit(**fields)


def apply_and_measure(keywords: List[str], proposal: Optional[dict] = None) -> dict:
    """Apply the pool and leave every keyword in it freshly measured.

    The two used to be separate chores and the second one had no prompt: a
    curation could hand the brand a pool whose incumbents had not been
    re-measured in weeks, so `_opportunity` scored them 0 and the weights that
    were just written did nothing. Anything already cached costs no request,
    so in practice this is a handful of calls for the keywords that survived
    the previous pool.

    Returns what it spent so the screen can say so rather than the marketer
    discovering it in the quota counter.
    """
    apply(keywords, proposal)
    pool = repo.get_brand_kit().get("seo_keywords") or []
    before = repo.naver_calls_today()
    # use_cache=True: 방금 조사에서 캐시에 들어간 후보는 다시 사지 않습니다.
    keyword_research.rank_keywords(pool, use_cache=True, include_trend=False)
    return {"pool": len(pool), "calls": repo.naver_calls_today() - before}


# --- 분야 추가 ---------------------------------------------------------------
# `propose` 는 모든 후보를 기존 풀과 한데 놓고 검색량 합계로 주제를 고릅니다.
# 그러면 검색량이 본래 작은 사업 분야(교육 등)는 매번 주력 상품에 밀려 빠지고,
# 씨앗을 바꿔도, TOPIC_LIMIT 를 올려도 결과가 그날 섞인 후보에 따라 흔들립니다.
# 분야 추가는 경쟁 단위를 '분야 안'으로 좁힙니다: 기존 풀은 건드리지 않고,
# 담당자가 이름 붙인 분야 안의 후보끼리만 겨뤄 상위 몇 개를 더합니다.

# 분야 하나가 풀을 잠식하지 않도록 — 분야가 늘수록 C-Rank 집중도는 흐려집니다.
CATEGORY_KEYWORD_LIMIT = 3
# 부수 사업은 검색량이 본래 작아 기본 하한(200)이면 핵심 검색어까지 걸러집니다.
# MIN_VIABLE_VOLUME(측정 불가 구간)보다는 충분히 위에 둡니다.
CATEGORY_MIN_VOLUME = 50
# 판정에 넘기는 후보 수(검색량 순). 사실상 전부입니다: 처음엔 80개로 잘랐더니
# 728개 중 상위 80개가 전부 취미 DIY(스퀴시만들기 등)였고, 정작 환경교육(187위)·
# 공예체험(363위)은 AI가 보지도 못했습니다. 응답에는 고른 것만 적게 해서 출력은 작습니다.
CATEGORY_JUDGE_LIMIT = 1000
# MAX_DOCS_PER_SEARCH(100)는 주력 상품 기준이라 부수 분야엔 맞지 않습니다. 실측에서
# 더스티치 답례품은 검색 1회당 글 9~39개, 교육은 가장 덜 붐비는 생태전환교육이 256개였고
# 41개 전부가 100을 넘었습니다. 분야 안에서는 덜 붐비는 순으로 고르고, 이 선 위만
# '사실상 노출 불가'로 뺍니다. 100을 넘는 채택분은 화면에서 따로 알립니다.
CATEGORY_MAX_DOCS_PER_SEARCH = 1000
# 경쟁도 조사(유료) 상한 — 분야 판정을 통과한 것만 잽니다. 부수 분야는 검색량 큰
# 일반어일수록 경쟁이 심해서(환경교육: 검색 1회당 글 2만 개), 이길 수 있는 건 아래쪽
# 세부어입니다. 그래서 판정 통과분은 웬만하면 전부 재도록 넉넉히 둡니다.
CATEGORY_SCORE_LIMIT = 50

CATEGORY_SEED_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 전략가입니다. 브랜드 정보와 담당자가 추가하려는 **사업 분야 하나**를 "
    "받아, 그 분야의 연관검색어를 끌어올 **씨앗 키워드 5개**를 제안하세요.\n\n"
    "절대 규칙:\n"
    "- 분야 이름을 그대로 쓰지 마세요. 브랜드가 그 분야에서 실제로 하는 일을, **고객이 네이버에 "
    "입력하는 말**로 바꾸세요. 예를 들어 꽃집의 '교육' 분야라면 '교육'이 아니라 "
    "'플라워클래스', '꽃꽂이수업' 같은 말입니다.\n"
    "- 5개 모두 그 분야 안에서 서로 다른 각도(대상·형태·목적 등)여야 합니다. "
    "다른 사업 분야의 말을 섞지 마세요.\n"
    "- 브랜드명·자체 용어, '친환경'·'수제'·'프리미엄' 같은 수식어를 붙이지 마세요. "
    "검색량이 거의 0이 되어 연관검색어가 나오지 않습니다.\n"
    "- 띄어쓰기 없는 2~6글자 일반 명사가 좋습니다.\n\n"
    '반드시 아래 JSON만 출력하세요: {"seeds": ["...", "...", "...", "...", "..."]}'
)

CATEGORY_JUDGE_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 전략가입니다. 브랜드 정보, 담당자가 추가하려는 사업 분야, "
    "키워드 후보 목록을 받아 **그 분야에서** 이 브랜드가 정직하게 글을 쓸 수 있는 키워드만 고릅니다.\n\n"
    "판정 기준:\n"
    "- 그 분야에 속하지 않는 후보는 브랜드의 다른 사업에 맞더라도 고르지 마세요.\n"
    "- 검색한 사람이 원하는 것을 이 브랜드가 줄 수 있어야 합니다. 단어가 비슷해도 브랜드가 하지 "
    "않는 종류의 서비스·상품·지역이면 고르지 마세요.\n"
    "- 검색 의도가 여러 갈래로 갈리는 광범위한 한 단어 일반어는 고르지 마세요.\n\n"
    "그룹(검색 의도):\n"
    "- purchase: 그 분야의 상품·서비스를 사거나 신청하려는 검색\n"
    "- division: 본 제품과 별개인 사업 부문(교육·B2B 등)을 이용하려는 검색\n"
    "- prospect: 아직 이용 의사는 없지만 브랜드와 접점이 되는 정보 탐색\n"
    "- identity: 검색량은 적지만 브랜드가 그 분야에서 무엇을 하는지 드러내는 말\n\n"
    "고른 키워드만 나열하세요. 고르지 않은 후보는 적지 않아도 됩니다.\n"
    "반드시 아래 JSON만 출력하세요. 설명이나 코드펜스를 붙이지 마세요.\n"
    '{"keywords": [{"keyword": "...", "group": "purchase|division|prospect|identity", '
    '"reason": "채택 이유 25자 이내"}]}'
)


def suggest_category_seeds(category: str, vendor: Optional[str] = None) -> List[str]:
    """Seeds for one business line, phrased the way its customers search.

    The person typing '교육' is naming a part of their business, not a search
    term; Naver has nothing useful to relate to the bare word. Translating it
    is the step the marketer could not be expected to know how to do.
    """
    raw = generate_text(
        vendor=vendor or get_configured_vendor(),
        prompt=f"{_brand_context()}\n\n추가할 사업 분야: {category}",
        system=CATEGORY_SEED_SYSTEM_PROMPT,
        max_tokens=500,
        note="keyword-category-seeds",
    )
    seeds = _parse(raw).get("seeds", [])
    return [s.strip() for s in seeds if isinstance(s, str) and s.strip()][:keyword_research.HINT_KEYWORD_LIMIT]


def find_category_candidates(
    category: str, *, current: Optional[List[str]] = None, vendor: Optional[str] = None
) -> dict:
    """Free stage: seeds → related keywords → keep only this category's.

    Relevance is judged before competition is measured, the reverse of the
    full sweep, because here most related keywords belong to other lines of
    business (a '체험' seed brings back farm stays and flight lessons) and
    measuring them would spend metered calls on words that cannot be kept.
    """
    category = category.strip()
    current = current or []
    seeds = suggest_category_seeds(category, vendor=vendor)
    if not seeds:
        raise RuntimeError("이 분야의 씨앗 키워드를 만들지 못했습니다. 분야 이름을 조금 더 구체적으로 적어보세요.")

    in_pool = {keyword_research.normalize(k) for k in current}
    found = [
        r for r in keyword_research.sweep_candidates(seeds, min_volume=CATEGORY_MIN_VOLUME)
        if keyword_research.normalize(r["keyword"]) not in in_pool
    ]
    offered = found[:CATEGORY_JUDGE_LIMIT]
    result = {"category": category, "seeds": seeds, "found": len(found), "relevant": []}
    if not offered:
        return result

    lines = "\n".join(f"- {r['keyword']} ({r['volume']})" for r in offered)
    prompt = (
        f"{_brand_context()}\n\n"
        f"추가하려는 사업 분야: {category}\n"
        f"이미 쓰고 있는 SEO 키워드(후보 아님): {', '.join(current) if current else '(없음)'}\n\n"
        f"키워드 후보 (괄호 안은 월검색량):\n{lines}"
    )
    raw = generate_text(
        vendor=vendor or get_configured_vendor(),
        prompt=prompt,
        system=CATEGORY_JUDGE_SYSTEM_PROMPT,
        max_tokens=4000,
        note="keyword-category-judge",
    )
    by_keyword = {r["keyword"]: r for r in offered}
    relevant = []
    for item in _parse(raw).get("keywords", []):
        keyword = (item.get("keyword") or "").strip()
        row = by_keyword.pop(keyword, None)
        if row is None:
            continue  # 제시하지 않은 말을 지어냈거나 중복
        group = item.get("group") if item.get("group") in INTENT_GROUPS else "prospect"
        relevant.append({**row, "group": group, "reason": item.get("reason", "")})
    result["relevant"] = relevant
    return result


def _category_score_order(relevant: List[dict]) -> List[dict]:
    return sorted(relevant, key=lambda r: r.get("volume") or 0, reverse=True)[:CATEGORY_SCORE_LIMIT]


def category_score_cost(relevant: List[dict]) -> int:
    """Metered requests `propose_category` would spend, after cache hits."""
    return keyword_research.estimate_sweep_cost(_category_score_order(relevant), limit=CATEGORY_SCORE_LIMIT)


def propose_category(relevant: List[dict], *, use_cache: bool = True) -> dict:
    """Metered stage: measure competition, keep the best few of this category.

    The same arithmetic guards as `propose` apply — relevance does not make
    an unmeasurable or hopelessly crowded term worth targeting — but nothing
    here competes with the existing pool or with other categories.

    Returns a proposal `apply` understands, so new keywords get their group's
    default weight and identity terms stay out of target selection.
    """
    meta = {r["keyword"]: r for r in relevant}
    to_score = _category_score_order(relevant)
    scored = keyword_research.rank_keywords(
        [r["keyword"] for r in to_score], use_cache=use_cache, include_trend=False
    )

    passed: List[dict] = []
    scoring = {r["keyword"] for r in to_score}
    excluded: List[dict] = [
        {**r, "estimated_volume": r.get("volume"), "reason": f"분야 후보 {CATEGORY_SCORE_LIMIT}개 초과(검색량 순)"}
        for r in relevant if r["keyword"] not in scoring
    ]
    for row in scored:
        info = meta.get(row["keyword"], {})
        group = info.get("group", "prospect")
        volume = row.get("estimated_volume") or 0
        docs = row.get("documents") or 0
        ratio = (docs / volume) if volume else None
        entry = {**row, "group": group, "reason": info.get("reason", ""), "ratio": ratio}
        if group != "identity":
            if volume and volume <= MIN_VIABLE_VOLUME:
                excluded.append({**entry, "reason": f"검색량 없음({volume}회 이하)"})
                continue
            if ratio is not None and ratio > CATEGORY_MAX_DOCS_PER_SEARCH:
                excluded.append({**entry, "reason": f"경쟁 과다(문서/검색 {ratio:.0f})"})
                continue
        passed.append(entry)

    # 점수(검색량 ÷ log 경쟁)로 고르면 log 가 경쟁을 거의 못 깎아, 가장 붐비는 일반어
    # (만들기체험: 검색 1회당 글 1.9만 개)가 위로 옵니다. 부수 분야에서 이길 수 있는 건
    # 덜 붐비는 세부어라 경쟁 낮은 순으로 고릅니다. 경쟁 미측정은 맨 뒤.
    passed.sort(key=lambda r: (r["ratio"] is None, r["ratio"] or 0, -(r.get("estimated_volume") or 0)))
    excluded.extend(
        {**r, "reason": f"분야당 {CATEGORY_KEYWORD_LIMIT}개까지 — 경쟁 낮은 순"}
        for r in passed[CATEGORY_KEYWORD_LIMIT:]
    )
    kept = passed[:CATEGORY_KEYWORD_LIMIT]

    groups: Dict[str, List[dict]] = {key: [] for key in INTENT_GROUPS}
    for r in kept:
        groups[r["group"]].append(r)
    return {
        "groups": groups,
        "excluded": excluded,
        "accepted": [r["keyword"] for r in kept],
    }
