"""직접 쓴 글 진단 — 앱 밖에서 쓴 블로그 글을 발행 전에 점검합니다.

Everything else in this app audits text the app itself generated. But the
marketer also writes posts by hand, and three of them, read from PDFs of the
live blog (2026-10), showed the same pattern the generated posts had been
fixed for, only more strongly:

* **Titles built from brand vocabulary nobody searches.** 「한복새활용 행복인형
  미니미- 수공예 키링 오픈」: 한복새활용 10 searches a month, 행복인형 15,
  수공예키링 15. The same product's searchable names — 노리개키링 1,350 a
  month at 6.7 competing posts per search, 한복키링 790, 색동키링 110 — were
  in neither the title nor the body.
* **Or the opposite: head terms no small blog can win.** 「명절 선물,
  기념품,외국인 선물… 고민 끝!!」 aims at 기념품 (745 posts per search) and
  외국인선물 (363) while 색동머리핀 (350 searches, 10 per search) sat unused.
* **Little text per photo**, with key facts (size, use) lettered onto images
  where search cannot read them, and banned superlatives ('세상에 하나뿐인')
  the generated posts are already protected from.

So this runs the same measurements on any title and body: keyword demand and
competition from the Naver APIs the keyword screens already use (cached, so a
re-check is mostly free), plus the deterministic text checks. It never
rewrites anything — the post is the marketer's — it reports and suggests.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ai_workers import keyword_research as kr
from ai_workers.guardrail import apply_blacklist_dictionary, check_certification_scope
from ai_workers.sentence_length import stats as sentence_stats

# Searches a month below which a title keyword brings no visitors.
MIN_VOLUME = 100
# Competing blog posts per monthly search. Calibrated on this blog's own
# measurements: 노리개키링 6.7, 색동머리핀 10 and 결혼답례품 11 are winnable;
# 한복키링 50 is a stretch; 기념품 745 and 외국인선물 363 are not.
EASY_RATIO = 30
HARD_RATIO = 100
# Characters of text (no spaces) per photo below which a post reads as a
# photo album to search — there is too little text to match a query to.
MIN_CHARS_PER_PHOTO = 120
# A 제품 글 is checked for the facts a buyer searches for.
MAX_TITLE_CANDIDATES = 10
MAX_SUGGESTION_LOOKUPS = 10
SUGGESTIONS = 6
# Candidates shown to the relevance judge — see suggest_keywords.
JUDGE_CANDIDATES = 150

# Words that describe the post, not the product — nobody searches them alone.
_TITLE_STOPWORDS = {
    "오픈", "추천", "후기", "안내", "소개", "고민", "끝", "이벤트", "출시", "신제품", "공지",
    "모집", "진행", "완료", "구매", "판매", "정보", "방법", "어때요", "선물추천",
}
_WORD_RE = re.compile(r"[가-힣A-Za-z0-9]+")
_SEGMENT_SPLIT_RE = re.compile(r"[,.!?·…\-–—|/()\[\]{}'\"“”‘’:;]+")
_TITLE_NOISE_RE = re.compile(r"[!?]{2,}|\.{3,}|…|[\U0001F300-\U0001FAFF]")

_PURCHASE_CHECKS = {
    "가격": re.compile(r"\d[\d,]*\s*원|\d+\s*만\s*원|가격"),
    "구매·주문 방법": re.compile(r"구매|주문|스토어|https?://|문의"),
    "크기·규격": re.compile(r"\d+(\.\d+)?\s*(cm|mm|센티|㎝)|크기|사이즈"),
    "제작·배송 기간": re.compile(r"배송|제작\s*기간|소요|영업일|일\s*이내"),
}
_SALES_HINT_RE = re.compile(r"구매|주문|가격|원\b|스토어|판매|답례품|키링|선물")
_SECTION_LINE_RE = re.compile(r"^\s*(\d+\s*[.)]|■|▶|●|◆|✔|✅|\[)")


def title_keyword_candidates(title: str) -> List[str]:
    """Searchable phrases in a title: words, and adjacent word pairs written
    together (Naver's search ads key '외국인 선물' as '외국인선물'). Pairs never
    cross punctuation — '선물, 기념품' is two thoughts, not one keyword."""
    pairs, singles = [], []
    for segment in _SEGMENT_SPLIT_RE.split(title or ""):
        words = [w for w in _WORD_RE.findall(segment)]
        for w in words:
            if len(w) >= 2 and w not in _TITLE_STOPWORDS:
                singles.append(w)
        for a, b in zip(words, words[1:]):
            if a in _TITLE_STOPWORDS or b in _TITLE_STOPWORDS:
                continue
            pairs.append(a + b)
    out = list(dict.fromkeys(pairs + singles))
    return out[:MAX_TITLE_CANDIDATES]


def _verdict(volume: int, ratio: Optional[float]) -> str:
    if volume < MIN_VOLUME:
        return "no_demand"
    # Head terms ('명절선물' 79,600, '키링' 41,930) can show a modest ratio and
    # still be unwinnable: shopping malls, brands and power blogs hold the
    # top slots. Same ceiling the keyword sweep uses.
    if volume > kr.DEFAULT_MAX_VOLUME:
        return "head"
    if ratio is None:
        return "unknown"
    if ratio <= EASY_RATIO:
        return "easy"
    if ratio <= HARD_RATIO:
        return "stretch"
    return "crowded"


VERDICT_LABELS = {
    "easy": "🟢 노려볼 만함",
    "stretch": "🟡 해볼 만함",
    "crowded": "🔴 경쟁 과열",
    "head": "🔴 대형 검색어 (쇼핑몰·대형 블로그 차지)",
    "no_demand": "⚪ 검색 거의 없음",
    "unknown": "❔ 측정 실패",
    "off_intent": "🟠 검색 의도가 다름",
}


def measure(keywords: List[str]) -> List[Dict]:
    """Monthly searches, blog documents and their ratio for each keyword."""
    keywords = [k for k in dict.fromkeys(keywords) if k]
    if not keywords:
        return []
    volumes = kr.keyword_volumes(keywords)
    rows = []
    for kw in keywords:
        volume = int(volumes.get(kw) or 0)
        try:
            documents = kr.blog_document_count(kw)
        except kr.NaverApiError:
            documents = None
        ratio = round(documents / volume, 1) if documents is not None and volume else None
        rows.append({
            "keyword": kw, "volume": volume, "documents": documents, "ratio": ratio,
            "verdict": _verdict(volume, ratio),
        })
    return rows


SEED_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 전략가입니다. 아래 블로그 글이 다루는 제품·서비스를, 그것을 사려거나 "
    "찾는 사람이 **네이버 검색창에 실제로 칠 말**로 5개 적으세요. 브랜드 고유 이름(제품명·브랜드명)은 "
    "검색되지 않으니 쓰지 말고, 일반 명사로 쓰세요(예: '행복인형 미니미' → '노리개키링', '한복키링'). "
    "띄어쓰기 없이 쓰세요.\n"
    '반드시 아래 JSON만 출력하세요: {"seeds": ["...", "...", "...", "...", "..."]}'
)

JUDGE_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 SEO 전략가입니다. 블로그 글과 키워드 후보 목록을 받아, **그 키워드로 검색한 "
    "사람이 이 글을 보고 만족할** 키워드를 모두 고르세요(보통 5~15개). 단어가 겹쳐도 다른 상품(예: "
    "캐릭터 키링, 자동차 키링, 식품 선물세트)이거나 다른 목적(예: '고구려'를 검색한 사람은 역사 정보를 "
    "찾습니다)이면 고르지 마세요. 검색 의도가 여러 갈래인 한 단어 일반어도 고르지 마세요.\n"
    '반드시 아래 JSON만 출력하세요: {"keywords": ["...", "..."]}'
)


def _weight(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _llm_json(prompt: str, system: str, note: str) -> dict:
    from ai_workers.multi_llm_router import generate_text, get_configured_vendor

    raw = generate_text(
        vendor=get_configured_vendor(), prompt=prompt, system=system, max_tokens=800, note=note,
    )
    cleaned = re.sub(r"```json\s*|```\s*$", "", (raw or "").strip())
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    return json.loads(match.group(0)) if match else {}


def suggest_keywords(
    title: str, body: str, title_rows: List[Dict], brand_kit: Optional[dict] = None
) -> tuple:
    """Searchable names for what this post is about, winnable first.
    Returns (suggestions, on_topic) — `on_topic` is the set of candidate
    keywords, title words included, that the model judged this post answers.

    Matching related searches by shared words was tried first and returned
    헬로키티키링 and 자동차키링 for a 한복 keyring, 육포선물세트 for a hair
    pin. So, the same steps as the keyword screen's [➕ 사업 분야 추가]: the
    model names how a buyer would search for this product, Naver expands that
    into related searches with volumes, and the model keeps only the ones this
    post would actually satisfy. Only those get the metered document count.

    Naver answers five seeds with ~500 related terms sorted by volume, and the
    busiest are unrelated head terms (소품샵, 미피, 김부각 for a keyring), so
    the judge sees a wide slice, with the brand's own pool (including words
    marked non-target — that flag is about titles of generated posts, not
    about whether a word is searched) and the title's own words first.
    Keywords the brand demoted below neutral weight (돌잔치답례품 0.3) are
    never suggested, the same rule that keeps them out of generated titles.
    """
    brand_kit = brand_kit or {}
    weights = brand_kit.get("keyword_weights") or {}
    demoted = {k.replace(" ", "") for k, w in weights.items() if _weight(w) < 1.0}
    pool = [k for k in brand_kit.get("seo_keywords") or [] if k.replace(" ", "") not in demoted]

    post = f"[제목] {title}\n[본문 앞부분]\n{(body or '')[:800]}"
    seeds = [
        s.replace(" ", "") for s in _llm_json(post, SEED_SYSTEM_PROMPT, "post-audit-seeds").get("seeds", [])
        if isinstance(s, str) and len(s.strip()) >= 2
    ][: kr.HINT_KEYWORD_LIMIT]

    pool_volumes = kr.keyword_volumes(pool) if pool else {}
    ordered = [{"keyword": k, "volume": int(pool_volumes.get(k) or 0)} for k in pool]
    ordered += [{"keyword": r["keyword"], "volume": r["volume"]} for r in title_rows]
    ordered += kr.discover_keywords(seeds) if seeds else []
    title_keys = {r["keyword"].replace(" ", "") for r in title_rows}

    candidates, seen = [], set()
    for r in ordered:
        key = r["keyword"].replace(" ", "")
        if key in seen or key in demoted:
            continue
        # Title words are judged whatever their volume — the point is to say
        # whether the title's own words match what searchers want.
        if key not in title_keys and not MIN_VOLUME <= r["volume"] <= kr.DEFAULT_MAX_VOLUME:
            continue
        seen.add(key)
        candidates.append(r)
    candidates = candidates[:JUDGE_CANDIDATES]
    if not candidates:
        return [], set()

    listing = "\n".join(f"- {r['keyword']} (월 {r['volume']})" for r in candidates)
    picked = _llm_json(f"{post}\n\n[키워드 후보]\n{listing}", JUDGE_SYSTEM_PROMPT, "post-audit-judge")
    by_key = {r["keyword"].replace(" ", ""): r for r in candidates}
    on_topic = {
        k.replace(" ", "") for k in picked.get("keywords", [])
        if isinstance(k, str) and k.replace(" ", "") in by_key
    }
    to_measure = sorted(
        (by_key[k] for k in on_topic if k not in title_keys),
        key=lambda r: -r["volume"],
    )[:MAX_SUGGESTION_LOOKUPS]
    rows = [r for r in measure([r["keyword"] for r in to_measure]) if r["verdict"] in ("easy", "stretch")]
    rows.sort(key=lambda r: (r["verdict"] != "easy", -kr.golden_score(r["volume"], r["documents"] or 10)))
    return rows[:SUGGESTIONS], on_topic


def _text_checks(title: str, body: str, photo_count: int, brand_kit: dict) -> Dict:
    text = re.sub(r"https?://\S+", "", body or "")
    chars = len(re.sub(r"\s", "", text))
    lines = [l for l in text.split("\n") if l.strip()]
    sections = sum(1 for l in lines if _SECTION_LINE_RE.match(l))
    sales = bool(_SALES_HINT_RE.search(f"{title}\n{text}"))
    purchase_missing = (
        [label for label, rx in _PURCHASE_CHECKS.items() if not rx.search(text)] if sales else []
    )
    _, hits = apply_blacklist_dictionary(f"{title}\n{text}", brand_kit.get("blacklist_map") or {})
    cert = check_certification_scope(text, brand_kit)
    return {
        "chars": chars,
        "photo_count": photo_count,
        "chars_per_photo": round(chars / photo_count) if photo_count else None,
        "sections": sections,
        "sentences": sentence_stats(text),
        "title_length": len(title or ""),
        "title_noise": _TITLE_NOISE_RE.findall(title or ""),
        "is_sales": sales,
        "purchase_missing": purchase_missing,
        "banned_terms": [{"term": h["forbidden"], "replacement": h["replacement"]} for h in hits],
        "cert_scope": [f["phrase"] for f in cert],
    }


def audit(title: str, body: str, photo_count: int = 0, brand_kit: Optional[dict] = None,
          use_api: bool = True) -> Dict:
    """Report for one hand-written post. `use_api=False` skips the Naver
    lookups (text checks only) — for when no key is registered."""
    from core import repo

    brand_kit = brand_kit if brand_kit is not None else repo.get_brand_kit()
    report = {"text": _text_checks(title, body, photo_count, brand_kit)}

    candidates = title_keyword_candidates(title)
    report["title_keywords"] = []
    report["suggestions"] = []
    report["api_error"] = None
    if use_api and candidates:
        try:
            report["title_keywords"] = measure(candidates)
        except kr.NaverApiError as exc:
            report["api_error"] = str(exc)
        if not report["api_error"]:
            try:
                report["suggestions"], on_topic = suggest_keywords(
                    title, body, report["title_keywords"], brand_kit
                )
                # A busy title word is only worth keeping if its searchers
                # want this post: 고구려 draws 13,690 searches for history.
                for row in report["title_keywords"]:
                    if row["verdict"] in ("easy", "stretch") and row["keyword"].replace(" ", "") not in on_topic:
                        row["verdict"] = "off_intent"
            except kr.NaverApiError as exc:
                report["api_error"] = str(exc)
            except Exception as exc:  # noqa: BLE001 — suggestions are optional
                report["suggest_error"] = str(exc)
        try:
            report["calls_left"] = kr.remaining_calls_today()
        except Exception:  # noqa: BLE001
            report["calls_left"] = None

    # Pool keywords the body already uses but the title doesn't carry.
    lowered_title = (title or "").lower()
    report["pool_in_body_not_title"] = [
        kw for kw in brand_kit.get("seo_keywords") or []
        if kw.lower() in (body or "").lower() and kw.lower() not in lowered_title
        and kw not in (brand_kit.get("non_target_keywords") or [])
    ]
    report["advice"] = _advice(report)
    return report


def _advice(report: Dict) -> List[str]:
    out = []
    t = report["text"]
    rows = report.get("title_keywords") or []
    good = [r for r in rows if r["verdict"] in ("easy", "stretch")]
    if rows and not good:
        dead = [r["keyword"] for r in rows if r["verdict"] == "no_demand"]
        crowded = [r["keyword"] for r in rows if r["verdict"] in ("crowded", "head")]
        off = [r["keyword"] for r in rows if r["verdict"] == "off_intent"]
        msg = "제목에 노출을 기대할 수 있는 검색어가 없습니다."
        if dead:
            msg += f" 검색이 거의 없는 말: {', '.join(dead[:4])}."
        if crowded:
            msg += f" 경쟁이 너무 센 말: {', '.join(crowded[:4])}."
        if off:
            msg += f" 검색하는 사람이 다른 것을 찾는 말: {', '.join(off[:4])}."
        out.append(msg)
    if good:
        first = sorted(good, key=lambda r: (r["verdict"] != "easy", -r["volume"]))[0]
        out.append(
            f"제목의 '{first['keyword']}'(월 {first['volume']:,}회)는 노려볼 만한 검색어입니다. "
            "제목 앞쪽으로 옮기고 본문에도 2~3회 쓰세요."
        )
    elif report.get("suggestions"):
        best = report["suggestions"][0]
        out.append(
            f"제목 앞쪽에 '{best['keyword']}'(월 {best['volume']:,}회, 검색 1회당 글 "
            f"{best['ratio']}개) 같은 실제 검색어를 넣고, 본문에도 2~3회 자연스럽게 쓰세요."
        )
    if report.get("pool_in_body_not_title"):
        out.append(
            "본문에는 있는데 제목에 없는 SEO 키워드: " + ", ".join(report["pool_in_body_not_title"][:4])
        )
    if t["title_noise"]:
        out.append("제목의 '!!', '…' 같은 기호는 빼는 편이 좋습니다 — 품질 낮은 글의 신호로 읽힐 수 있습니다.")
    if not 20 <= t["title_length"] <= 30:
        out.append(f"제목이 {t['title_length']}자입니다. 모바일 검색 결과에서 잘리지 않는 20~30자를 권합니다.")
    if t["chars_per_photo"] is not None and t["chars_per_photo"] < MIN_CHARS_PER_PHOTO:
        out.append(
            f"사진 {t['photo_count']}장에 글 {t['chars']:,}자(사진당 {t['chars_per_photo']}자)입니다. "
            "사진마다 무엇을 보여주는지 2~3문장씩 글로 써 주세요. 이미지 안에 넣은 글씨는 검색에 잡히지 않습니다."
        )
    elif t["chars"] < 800:
        out.append(f"본문이 {t['chars']:,}자(공백 제외)로 짧습니다. 검색어와 맞춰 볼 글이 적으면 노출이 어렵습니다.")
    if t["purchase_missing"]:
        out.append("구매하려는 독자가 찾는 정보가 빠졌습니다: " + ", ".join(t["purchase_missing"]))
    for hit in t["banned_terms"]:
        out.append(f"표시·광고 주의 표현 '{hit['term']}' → '{hit['replacement']}'로 바꾸세요.")
    for phrase in t["cert_scope"]:
        out.append(f"인증 범위를 넘는 표현: '{phrase[:40]}…' — 인증받은 품목명을 직접 쓰세요.")
    if t["sentences"].get("long", 0) >= 3:
        out.append(f"60자 넘는 긴 문장이 {t['sentences']['long']}개 있습니다. 짧게 나누면 읽기 쉬워집니다.")
    return out
