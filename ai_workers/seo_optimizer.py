"""Naver Blog SEO keyword density check + auto-rebalance.

`build_system_prompt()` already *instructs* the LLM to place SEO keywords
3~5 times, but an instruction is not a guarantee. This module verifies the
result deterministically and, only if it's off-target, asks the LLM for one
corrective pass — the same "verify, don't just instruct" pattern used for
the compliance guardrail.
"""
from __future__ import annotations

import re

from ai_workers import keyword_research
from ai_workers.multi_llm_router import generate_text
from ai_workers.title_variety import avoidance_instruction, closest_previous
from core import repo

MIN_COUNT = 2
MAX_COUNT = 6

# A brand keyword list is a *pool* of everything the brand wants to rank for;
# a single post can only be about a few of them. Enforcing every keyword on
# every post meant a 1,300-character article carrying 10 keywords x MIN_COUNT
# = 20 mandated phrases — one every ~65 characters, which reads as keyword
# stuffing and is over-optimization by Naver's own guidance. Naver ranks a
# focused post higher, so each post targets at most this many.
MAX_TARGETS_PER_POST = 3

# ...but "at most" was being read as "always". Every post in a five-post batch
# came back with exactly three targets and exactly MIN_COUNT occurrences of
# each, including a post about a teacher-training course carrying 돌잔치답례품
# and 결혼답례품. A cap is not a quota: a post about one thing should target
# one keyword.
#
# A keyword earns a target slot by being something the draft returned to on
# its own. One passing mention is the writer brushing past a term, and
# promoting that to a target is what puts the density pass to work inflating
# it to the floor and the title pass to work putting it in the headline.
MIN_MENTIONS_TO_TARGET = 2

REBALANCE_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 편집자입니다. 아래 텍스트에서 특정 키워드가 너무 적거나 "
    "너무 많이 등장합니다. 문장의 자연스러움과 원래 의미, 톤을 해치지 않는 선에서 "
    "키워드 노출 빈도만 조정한 전체 텍스트를 그대로 출력하세요. 텍스트 중간에 "
    "`[IMAGE: 경로]` 형식의 태그가 있다면, 이것은 사진이 삽입될 위치를 표시하는 마크업이므로 "
    "절대 삭제하거나 수정하거나 다른 곳으로 옮기지 말고 원래 있던 위치에 글자 그대로 "
    "유지하세요. 다른 설명은 절대 덧붙이지 마세요."
)

# Naver's ranking weights a keyword's presence in the title far above the same
# keyword sitting in the body, so a title that satisfies body-level density
# but contains zero target keywords is still an SEO miss — this is checked
# and corrected separately from `check_keyword_density`, which only sees the
# combined title+body count and would happily call that case "optimal".
# Enough for the scorer to have a real choice without turning one corrective
# pass into a long generation — past five, candidates start repeating each
# other's shape and the extra tokens buy nothing.
TITLE_CANDIDATES = 5

# Naver truncates blog titles in mobile search results at roughly thirty
# Korean characters, and a title under twenty rarely carries the keyword plus
# enough context to earn the click. Scored as a band, not a cliff.
TITLE_IDEAL_MIN = 20
TITLE_IDEAL_MAX = 30

TITLE_REWRITE_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 편집자입니다. 아래 제목을 자연스러움과 원래 의미를 해치지 않는 "
    "선에서, 주어진 키워드 후보 중 최소 1개가 반드시 포함되도록 다시 쓴 제목을 "
    f"**{TITLE_CANDIDATES}개** 제안하세요.\n\n"
    "조건:\n"
    "- 각 후보는 서로 확실히 다른 문장 구조로 쓰세요. 어미만 바꾼 변형은 쓸모가 없습니다.\n"
    "- 키워드를 제목 **앞쪽**에 두는 안을 반드시 몇 개 포함하세요.\n"
    f"- {TITLE_IDEAL_MIN}~{TITLE_IDEAL_MAX}자 사이로 쓰세요.\n"
    "- 낚시성 표현, 느낌표 남발, 이모지를 쓰지 마세요.\n\n"
    "번호나 따옴표, 설명 없이 **제목만 한 줄에 하나씩** 출력하세요."
)


def count_keyword_occurrences(text: str, keywords: list[str]) -> dict[str, int]:
    lowered = text.lower()
    return {kw: lowered.count(kw.lower()) for kw in keywords if kw}


# What a visit from this keyword is worth to the business, relative to an
# average one. Multiplied into the opportunity score, so a keyword can be kept
# in the pool for its traffic while being told it converts poorly.
#
# Traffic and revenue are not the same ranking. '돌잔치답례품' measures 22,150
# searches a month and wins every slot on that alone, but the searcher behind
# it wants a bulk, low-unit-price favour and bounces off a 1~3만원대 handmade
# piece; a 기관 굿즈제작 enquiry is a fraction of the volume and an order of
# magnitude of the revenue. Volume ÷ competition cannot see that difference —
# only the brand can, so it is an input, not something inferred.
DEFAULT_KEYWORD_WEIGHT = 1.0


def _weight_of(keyword: str, weights: dict | None) -> float:
    try:
        return float((weights or {}).get(keyword, DEFAULT_KEYWORD_WEIGHT))
    except (TypeError, ValueError):
        return DEFAULT_KEYWORD_WEIGHT


def title_candidates(
    keywords: list[str], weights: dict | None, excluded: list[str] | None = None
) -> list[str]:
    """The keywords allowed to claim the title, as opposed to the body.

    A weight below the neutral default is the brand saying this traffic
    converts worse than average. That is survivable in the body — the phrase
    appears, the post picks up the search, nobody is misled. In the title it
    is not: the title is the whole promise of the post, and the first batch
    run with modes produced '결혼답례품 고민이라면 DDP 서울패션마켓 더봄봄 방문'
    for a festival announcement and '어린이집답례품 추천 …' for a keyring
    launch. Both keywords are demoted to 0.3 in the brand kit; both took the
    title anyway, because weight only reordered the candidates and every
    stage that writes a title was handed the unfiltered pool.

    Demotion is not exclusion, so these keywords stay in the pool for the
    draft and for density: `select_target_keywords` still ranks them, the
    news sweep still searches them. They simply cannot be what the post
    announces itself as.

    `excluded` is the second, separate reason a keyword cannot hold a title:
    brand vocabulary. '더봄봄' draws 15 searches a month and '한복 새활용' 10,
    so a title built on either forfeits the exposure the title exists to win —
    but both must keep appearing in the body, and both are needed in the pool
    so the variety check knows they are allowed to repeat across titles. They
    are not demoted, because weight means what that traffic is worth and brand
    traffic is worth plenty; there just isn't any. Two different problems, two
    different inputs.

    Returns [] when every keyword is ruled out, and callers must handle that
    by leaving the title alone — forcing the least-bad one back in would
    reinstate exactly the behaviour this removes.
    """
    blocked = set(excluded or ())
    return [
        kw for kw in keywords
        if kw not in blocked and _weight_of(kw, weights) >= DEFAULT_KEYWORD_WEIGHT
    ]


def _opportunity(keyword: str, weights: dict | None = None) -> float:
    """What this keyword is worth to the business, from cache only.

    Reads whatever the Settings screen's keyword sweep already measured and
    never calls Naver: this runs inside article generation, where a metered
    request would add latency the writer is waiting on, spend quota nobody
    approved, and turn an API outage into a failed draft. An unmeasured
    keyword simply scores 0 and the caller falls back to draft frequency.

    Both halves of the ratio must be measured. Passing an absent document
    count through as zero made `golden_score` divide by log10(10) = 1, so a
    keyword with no competition data outscored an identical, fully measured
    one by up to 5.6x — competition unknown was silently read as competition
    none. That is the opposite of the safe default, and it fires precisely
    when a keyword has just been added and not yet swept.
    """
    volume = repo.get_cached_metric(
        keyword_research.normalize(keyword), "ad_volume", keyword_research.VOLUME_CACHE_DAYS
    )
    if volume is None:
        return 0.0
    # Document counts are cached under the keyword as searched, not the
    # 검색광고-normalized form — see keyword_research.normalize.
    documents = repo.get_cached_metric(
        keyword, "blog_total", keyword_research.DOCUMENT_CACHE_DAYS
    )
    if documents is None:
        return 0.0
    return keyword_research.golden_score(float(volume), int(documents)) * _weight_of(keyword, weights)


def competing_keywords(keywords: list[str], non_targets: list[str] | None) -> list[str]:
    """The pool minus the words the brand isn't trying to rank for.

    Brand vocabulary stays in `seo_keywords` because other machinery needs it
    there — `title_variety` reads the pool to know which words are allowed to
    repeat across titles, and dropping '더봄봄' from it would make every title
    carrying the brand name look like a repeat of the last one. What it must
    not do is take a slot in the competition: it is in the text either way.
    """
    blocked = set(non_targets or ())
    return [kw for kw in keywords if kw not in blocked]


def select_target_keywords(
    title: str, draft: str, keywords: list[str], limit: int = MAX_TARGETS_PER_POST,
    weights: dict | None = None, min_mentions: int = MIN_MENTIONS_TO_TARGET,
    non_targets: list[str] | None = None,
) -> list[str]:
    """The keywords this particular post is actually about, best bets first.

    Relevance is still decided by the draft: the writing model has just read
    the memo, the article and the photo captions, and the keywords it reached
    for unprompted are the ones the post genuinely covers. A keyword it never
    used is off-topic here — forcing '한복 기부' into a post about a trade-show
    conversation is what produced the stuffed drafts this replaced.

    What the draft cannot know is which of those keywords anyone searches for.
    '결혼답례품' draws 19,620 searches a month against 11 competing posts per
    search; '굿즈제작' draws 15,200 against 61. Both may appear in the same
    article, and before this the one the writer happened to repeat more often
    won the title. Among the keywords the draft actually used, ranking is now
    by opportunity — measured demand divided by measured competition — with
    frequency demoted to a tie-breaker.

    Keywords the sweep has never measured score 0, so a brand that has not run
    it yet gets exactly the previous frequency-ordered behaviour.

    `weights` scales each keyword's score by what its traffic is actually
    worth to the brand — see DEFAULT_KEYWORD_WEIGHT. A weight of 0 keeps the
    keyword in the pool (the draft may still use it, and the news search still
    searches it) while making sure it never takes a target slot.

    `non_targets` removes brand vocabulary before ranking — see
    `competing_keywords`. Without it the ranking is decided by whichever words
    the draft repeats most, and the words a brand repeats most are its own
    name and its own category: a batch run with every other fix in place still
    produced targets of ['더봄봄', '한복 새활용'] on four posts out of five,
    which between them draw 25 searches a month.

    **May return an empty list, and callers must handle that.** It means the
    post's subject has no home in the keyword pool. The previous fallback
    forced the pool's best-scoring keyword onto such a post so that "a post is
    never published with no SEO intent at all"; in practice that took an
    article about a teacher-training course, handed it 돌잔치답례품 because
    the phrase appeared once in passing, inflated it to the density floor and
    put it in the title. No SEO intent is the honest outcome there, and it
    surfaces the real gap — 더스티치 sells education services and the pool
    contains no education keyword — instead of hiding it behind a post that
    ranks for nothing and reads as a product ad.
    """
    keywords = competing_keywords(keywords, non_targets)
    if not keywords:
        return []
    counts = count_keyword_occurrences(f"{title}\n{draft}", keywords)
    # Opportunity first, then how central the keyword is to this particular
    # draft, then the longer (more specific, less contested) phrase.
    used = sorted(
        ((kw, n) for kw, n in counts.items() if n > 0),
        key=lambda kv: (-_opportunity(kv[0], weights), -kv[1], -len(kv[0])),
    )
    return [kw for kw, n in used if n >= min_mentions][:limit]


def check_keyword_density(
    title: str, content: str, keywords: list[str],
    minimum: int = MIN_COUNT, maximum: int = MAX_COUNT,
) -> dict:
    if not keywords:
        return {"status": "no_keywords", "counts": {}, "missing": [], "overused": []}

    counts = count_keyword_occurrences(f"{title}\n{content}", keywords)
    missing = [kw for kw, c in counts.items() if c < minimum]
    overused = [kw for kw, c in counts.items() if c > maximum]

    status = "optimal" if not missing and not overused else ("low" if missing else "high")
    return {"status": status, "counts": counts, "missing": missing, "overused": overused}


def title_keyword_coverage(title: str, keywords: list[str]) -> list[str]:
    """Target keywords that appear nowhere in the title itself."""
    lowered = title.lower()
    return [kw for kw in keywords if kw and kw.lower() not in lowered]


def score_title(
    title: str, keywords: list[str], history: list[str] | None = None,
    ignore_terms: list[str] | None = None,
) -> tuple[float, dict]:
    """How well a title serves Naver search, 0.0~1.0, with its breakdown.

    Deterministic on purpose. Asking a model to rank its own five candidates
    costs a second call to get an opinion, whereas every criterion that
    actually moves a Naver blog title is measurable: does it carry the
    keyword, how early, is it inside the length the results page shows, and
    is it a shape this blog has already published. The breakdown is returned
    so a surprising pick can be explained rather than trusted.
    """
    present = [kw for kw in keywords if kw and kw.lower() in title.lower()]
    if not present:
        # Disqualified: the whole point of this pass is keyword coverage.
        return 0.0, {"keyword": False}

    # Earliest keyword occurrence — Naver weights terms near the front, and a
    # keyword trailing after a long lyrical clause reads as an afterthought.
    first_at = min(title.lower().index(kw.lower()) for kw in present)
    position = 1.0 - min(first_at / max(len(title), 1), 1.0)

    length = len(title)
    if TITLE_IDEAL_MIN <= length <= TITLE_IDEAL_MAX:
        length_score = 1.0
    else:
        edge = TITLE_IDEAL_MIN if length < TITLE_IDEAL_MIN else TITLE_IDEAL_MAX
        length_score = max(0.0, 1.0 - abs(length - edge) / 15)

    variety = 1.0
    if history:
        _, similar = closest_previous(title, history, ignore_terms or list(keywords))
        variety = 1.0 - similar

    # Clickbait punctuation and emoji read as low-quality to readers and are
    # the shape Naver's spam signals look for. Applied as a multiplier rather
    # than a weighted term: as one more addend it was worth 0.06, so
    # "결혼답례품!!! 대박 ... 지금 확인 ..." scored second-highest on the
    # strength of its keyword placement. A spammy title should be discounted
    # whatever else it gets right — the brand's own tone rules ban the
    # exclamation marks outright.
    noise = len(re.findall(r"[!?]{2,}|\.{3,}|[\U0001F300-\U0001FAFF]", title))
    cleanliness = max(0.0, 1.0 - 0.3 * noise)

    total = (0.45 * position + 0.30 * length_score + 0.25 * variety) * cleanliness
    return total, {
        "keyword": True, "position": round(position, 2), "length": length,
        "length_score": round(length_score, 2), "variety": round(variety, 2),
        "cleanliness": round(cleanliness, 2), "total": round(total, 3),
    }


def rewrite_title_for_keyword(
    title: str, content_snippet: str, keywords: list[str], vendor: str,
    history: list[str] | None = None,
) -> str:
    """Slot a target keyword into an AI-generated title, best candidate wins.

    One call produces several candidates rather than one, because the model
    writing a single title has no basis for comparison — it cannot know that
    its phrasing puts the keyword eighteen characters in, or that it just
    reproduced last week's headline. Generating alternatives and scoring them
    separates writing from judging, and the judging is cheap and explainable.

    Best-effort throughout: an LLM failure, or five candidates that all drop
    the keyword, keeps the original title rather than risk a worse one.
    """
    prompt = (
        f"[원래 제목]\n{title}\n\n[본문 도입부]\n{content_snippet}\n\n"
        f"[포함할 키워드 후보]\n{', '.join(keywords)}"
        + avoidance_instruction(history or [])
    )
    try:
        raw = generate_text(
            vendor=vendor, prompt=prompt, system=TITLE_REWRITE_SYSTEM_PROMPT,
            max_tokens=400, note="seo-title-rewrite",
        )
    except Exception:
        return title

    candidates = [
        re.sub(r'^\s*[\d]+[.)]\s*', "", line).strip().strip('"').strip("'")
        for line in (raw or "").splitlines()
    ]
    candidates = [c for c in candidates if c][:TITLE_CANDIDATES]
    if not candidates:
        return title

    best, best_score = None, 0.0
    for candidate in candidates:
        score, _ = score_title(candidate, keywords, history, list(keywords))
        if score > best_score:
            best, best_score = candidate, score
    return best or title


def rebalance_keywords(
    title: str, content: str, keywords: list[str], vendor: str,
    minimum: int = MIN_COUNT, maximum: int = MAX_COUNT, enforce: bool = True,
) -> tuple[str, dict]:
    """Returns (final_content, density_report). Only rebalances the body —
    the title is left untouched to avoid the fragility of asking an LLM to
    return title+body combined and splitting it back apart. Only calls the
    LLM when density is actually off-target; otherwise returns content as-is.

    `enforce=False` measures and reports without rewriting. That is the
    '내용 우선' mode: the marketer still gets to see which keywords the post
    landed on and how often, but nothing is inflated to a floor. It also
    saves the corrective LLM call.
    """
    report = check_keyword_density(title, content, keywords, minimum, maximum)
    if not enforce:
        report["enforced"] = False
        return content, report
    if report["status"] in ("optimal", "no_keywords"):
        return content, report

    issue_desc = []
    if report["missing"]:
        issue_desc.append(f"부족한 키워드(각 {minimum}회 이상 필요): {', '.join(report['missing'])}")
    if report["overused"]:
        issue_desc.append(f"과다 사용된 키워드(각 {maximum}회 이하로 축소): {', '.join(report['overused'])}")

    prompt = f"[본문]\n{content}\n\n[조정 필요 사항]\n" + "\n".join(issue_desc)
    try:
        # The whole body comes back, so the ceiling scales with it (~1 token
        # per Korean character with spaces, plus headroom) instead of a
        # fixed 2500 that a 2,000자 post would overrun.
        raw = generate_text(
            vendor=vendor, prompt=prompt, system=REBALANCE_SYSTEM_PROMPT,
            max_tokens=max(2500, int(len(content) * 1.2) + 500), note="seo-rebalance",
        )
    except Exception:
        # Best-effort; never block the pipeline. This includes a response
        # cut off at the output ceiling (multi_llm_router raises for that) —
        # a truncated post must never replace the full one.
        return content, report

    new_content = raw.strip() or content
    # The pass only moves keywords around; it has no reason to lose a
    # meaningful share of the post. A result this much shorter is a model
    # that summarised or stopped early without saying so — keep the original.
    if len(new_content) < len(content) * REBALANCE_MIN_LENGTH_RATIO:
        print(
            f"[seo_optimizer] rebalance shrank the body {len(content)}→{len(new_content)} chars — "
            "keeping the original"
        )
        return content, report
    return new_content, check_keyword_density(title, new_content, keywords, minimum, maximum)


# See the length check at the end of rebalance_keywords.
REBALANCE_MIN_LENGTH_RATIO = 0.85
