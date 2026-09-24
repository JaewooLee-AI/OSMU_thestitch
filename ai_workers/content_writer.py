"""The one content pipeline: source material -> 4-channel OSMU output.

OSMU_admin had two near-identical pipelines (news_scraper.run_newsjacking_pipeline
and manual_content_writer.run_manual_content_pipeline) that differed only in
where the seed text came from and whether a source-link footer was appended.
They're one function here, with `source_type` selecting those two differences.

Runs **synchronously in the desktop app's process** (on a background thread
started by the workbench view), which is the main structural change from the
original architecture. The async Supabase queue + `worker_runner.py` polling
loop existed to route around Vercel's 10-second serverless timeout; with the
UI and the engine in the same Python process there is no timeout to route
around, and a queue whose producer and consumer are the same process is pure
overhead. `progress` is a callback so the caller can show which stage is
running instead of the marketer staring at a spinner for 90 seconds.

Stage order, and why:
  1. caption photos      — cached/batched (ai_workers/vision.py)
  2. draft the body      — the only call that sees memo + article + captions
  3. compliance guardrail— rewrites, so it must run before density checks
  4. SEO rebalance       — verifies what stage 2 was merely *instructed* to do
  5. dictionary re-sweep — stages 3~4 are fresh LLM output; re-run the cheap
                           deterministic substitution over them
  6. image-tag backstops — nothing may silently drop an attached photo
  7. secondary channels  — Instagram / X / Shorts / Naver tags, best-effort
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from ai_workers import body_variety, content_mode
from ai_workers.guardrail import (
    apply_blacklist_dictionary,
    apply_guardrail_if_enabled,
    check_certification_scope,
    filter_certification_hashtags,
    remove_certification_overclaims,
)
from ai_workers.instagram_caption_writer import write_instagram_caption
from ai_workers.multi_llm_router import generate_text, get_configured_vendor
from ai_workers.naver_hashtag_writer import write_naver_hashtags
from ai_workers.news_scraper import scrape_article
from ai_workers.photo_placement import (
    caption_attachments,
    ensure_all_photos_tagged,
    ensure_image_tags_preserved,
    strip_unresolvable_image_tags,
)
from ai_workers.prompt_builder import (
    SUBJECT_INSTRUCTION,
    build_blog_system_prompt,
    notice_block,
    photo_context,
    photo_instruction,
    recent_posts_block,
)
from ai_workers import factsheet
from ai_workers import search_intent
from ai_workers.proofreader import proofread
from ai_workers.seo_optimizer import (
    check_keyword_density,
    rebalance_keywords,
    rewrite_title_for_keyword,
    select_target_keywords,
    title_candidates,
    title_keyword_coverage,
)
from ai_workers.shorts_writer import write_shorts_script
from ai_workers.sns_validator import validate_instagram, validate_tweets
from ai_workers.title_variety import (
    SIMILARITY_THRESHOLD,
    avoidance_instruction,
    closest_previous,
    rewrite_for_variety,
)
from ai_workers.x_thread_writer import write_x_thread
from core import repo

def _title_generation_instruction(seo_keywords: List[str], source_title: str = "") -> str:
    """Asks the LLM to draft the title alongside the body in the same call
    (see module docstring on why title+body aren't split across two calls).
    When SEO keywords are configured, the instruction front-loads keyword
    inclusion here — cheaper than fixing it after the fact — and
    `rewrite_title_for_keyword` below is the verify/correct backstop for
    when the model ignores it anyway."""
    keyword_line = ""
    if seo_keywords:
        keyword_line = (
            f" 다음 타깃 키워드 중 최소 1개를 제목에 자연스럽게 포함하세요: {', '.join(seo_keywords)}."
        )
    if source_title:
        # Newsjacking: the value is the brand's angle on the news, not the
        # news itself. Naver pools blog and news for duplicate detection, so
        # echoing the publisher's headline gets the post dropped in favour of
        # the original — and a reader searching that headline wants the
        # article, not a product post.
        keyword_line += (
            f"\n원본 기사 제목은 «{source_title}» 입니다. 이 제목을 그대로 쓰거나 살짝 바꿔 "
            "쓰지 마세요 — 네이버는 블로그와 뉴스를 같은 유사문서 판정 대상으로 보기 때문에 "
            "기사 제목을 따라 쓰면 원본 기사에 밀려 검색에서 제외됩니다. "
            "뉴스의 핵심 소재나 수치는 가져오되, 제목은 '우리 브랜드가 이 뉴스를 어떻게 "
            "해석했는가'가 드러나도록 새로 지으세요."
        )
    return (
        "\n\n[제목 자동 생성 안내] 담당자가 제목을 입력하지 않았습니다. 본문보다 먼저, 첫 줄에 "
        "정확히 `[TITLE: 생성한 제목]` 형식으로 이 포스트에 어울리는 제목을 **25자 이내로** 출력한 "
        "뒤, 줄바꿈하고 그 다음부터 본문을 이어서 작성하세요. 부제목이나 설명을 덧붙이지 말고 "
        f"제목 하나만 짧게 쓰세요.{keyword_line}"
    )

# 이 아래면 '검색해서 들어온 사람이 답을 못 찾는 글'로 봅니다. 절반은 임의의
# 선이지만, 노출을 목표로 하는 글에서 기대 항목의 절반도 답하지 못한다면 그건
# 체류시간으로 드러나고 순위로 돌아옵니다 — _recommendation 참고.
INTENT_COVERAGE_FLOOR = 50

_TITLE_MARKER_RE = re.compile(r"^\s*\[TITLE:\s*(.+?)\]\s*\n+", re.IGNORECASE)

Progress = Optional[Callable[[str], None]]


def _extract_generated_title(draft: str):
    """Pulls a leading `[TITLE: ...]` marker off the draft, if present.
    Returns (title_or_None, remaining_draft)."""
    match = _TITLE_MARKER_RE.match(draft)
    if not match:
        return None, draft
    return match.group(1).strip(), draft[match.end():]


def _recommendation(report: Dict) -> Dict:
    """A deterministic verdict from fields the pipeline already computed —
    not another LLM call — so the marketer gets an instant, reproducible
    answer to "should I regenerate this?" instead of having to read every
    sub-section of the report and weigh it themselves."""
    reasons = []
    if report.get("audit_failed"):
        # Not "N건의 이슈": nothing was found — nothing was *checked*. Saying
        # so plainly keeps the marketer from hunting for a violation that
        # isn't there, and from shipping on the assumption there is none.
        reasons.append("컴플라이언스 검수를 완료하지 못함 — 검수만 다시 실행 필요")
    elif report.get("compliance_pass") is False:
        reasons.append(f"컴플라이언스 미해결 이슈 {len(report.get('llm_issues') or [])}건")

    density = report.get("seo_density") or {}
    conflicts = report.get("seo_conflicts") or []
    # In '내용 우선' the density numbers are reported for information only —
    # the marketer chose not to enforce them, so being under the floor is the
    # requested outcome, not a defect to regenerate over.
    if density.get("enforced") is not False:
        # Missing keywords the guardrail stripped are excluded here: another
        # run can't satisfy them, so listing them as a reason to regenerate
        # would send the marketer into a loop.
        fixable_missing = [kw for kw in density.get("missing") or [] if kw not in conflicts]
        if fixable_missing:
            reasons.append(f"SEO 키워드 부족: {', '.join(fixable_missing)}")
        if density.get("overused"):
            reasons.append(f"SEO 키워드 과다: {', '.join(density['overused'])}")

    # No target at all is not a failure of this draft — it means the post's
    # subject has no matching keyword in the brand pool (see
    # select_target_keywords). Regenerating cannot fix that; adding a keyword
    # for that line of business can, so it routes to 'settings'.
    #
    # Not reported in '내용 우선': that mode never shows the model the pool,
    # so an empty target list is the mode working as asked, not a gap in the
    # keyword list.
    no_targets = (
        report.get("seo_targets") == []
        and bool(report.get("seo_pool"))
        and report.get("content_mode") != "rich"
    )

    # 검색 의도 미충족은 이 글의 결함이 아니라 입력의 공백입니다. 답례품을
    # 검색한 사람은 가격·최소수량·제작기간·주문 방법을 확인하러 오는데, 메모에
    # 그 숫자가 없으면 어떤 모델도 쓸 수 없습니다. 첫 측정에서 돌답례품 글이
    # 충족도 0%로 나왔고, 그 여섯 가지가 전부 비어 있었습니다.
    #
    # **다시 생성하라고 하지 않습니다.** 재생성으로는 없는 가격이 생기지 않고,
    # 그렇게 안내하면 컴플라이언스 오탐이 만들었던 것과 같은 무한 루프가 됩니다.
    # 채워야 할 입력으로 안내하는 것이 정확한 처방입니다.
    #
    # 노출이 목표라서 더 중요합니다 — 네이버 DIA는 체류시간을 보고, 검색해서
    # 들어온 사람이 답을 못 찾고 나가면 그 신호가 순위를 도로 깎습니다.
    # 순수 공지 글(공지 정보는 채웠는데 제품 정보는 안 채운 글)에서는 이 경고를
    # 아예 띄우지 않습니다. 강사과정 공지 글이 SEO 타깃으로 '굿즈제작'을 물고
    # 들어가면, 검색 의도 감사는 '이 글이 최소 제작 수량·단가·배송 기간에
    # 답하는가'를 묻고 0%를 냈다 — 강사과정 공지에는 애초에 해당하지 않는
    # 질문이다. 게다가 안내 문구는 그 항목을 🛍️ 제품·주문 정보에 채우라고
    # 하는데, 공지 글에 제품 판매 정보를 채우라는 건 맥락에 안 맞는 지시다.
    # 근본 원인(교육 키워드가 SEO 풀에 없어서 무관한 키워드가 타깃이 되는 것)은
    # 풀 재구성으로 풀어야 하고, 여기서는 그 결과로 나온 오해의 소지가 있는
    # 경고만 막는다.
    is_pure_notice = bool((report.get("notice") or {}).get("checked")) and not bool(
        (report.get("product") or {}).get("checked")
    )
    intent = report.get("search_intent") or {}
    intent_coverage = intent.get("coverage")
    intent_gap = bool(
        intent.get("checked")
        and intent_coverage is not None
        and intent_coverage < INTENT_COVERAGE_FLOOR
        and not is_pure_notice
    )
    fact_gaps = []
    for key, label in (("notice", "공지 정보"), ("product", "제품·주문 정보")):
        section = report.get(key) or {}
        if section.get("checked") and section.get("missing_labels"):
            fact_gaps.append(f"{label}: {', '.join(section['missing_labels'])}")

    # 최근 글과 본문이 겹치는 것도 재생성 사유가 아니라 입력의 공백입니다 — 같은
    # 메모로 다시 만들면 같은 글이 나옵니다. 이 제품만의 디테일을 메모에 채우라는
    # 안내로 보냅니다 (검색 의도 미충족과 같은 이유).
    variety = report.get("body_variety") or {}
    body_similar = bool(variety.get("similar_to"))

    # 목표보다 한참 짧은 글도 같은 처방입니다. 분량을 채울 재료(제품 설명)가
    # 메모에 없으면 다시 생성해도 같은 길이이거나, 브랜드 소개로 채워집니다.
    length = report.get("length") or {}
    too_short = bool(length.get("short"))

    if reasons:
        verdict = "regenerate"
    elif conflicts or no_targets or intent_gap or fact_gaps or body_similar or too_short:
        verdict = "settings"
    else:
        verdict = "ok"
    return {
        "verdict": verdict,
        "reasons": reasons,
        "conflicts": conflicts,
        "no_targets": bool(no_targets),
        "intent_gap": intent_gap,
        "intent_coverage": intent_coverage,
        "intent_missing": intent.get("missing") or [],
        "fact_gaps": fact_gaps,
        "body_similar_to": variety.get("similar_to") if body_similar else None,
        "body_similarity": variety.get("score") if body_similar else None,
        "too_short": too_short,
        "length_chars": length.get("chars"),
        "length_target": length.get("target"),
    }


# Below this share of the target's lower bound a post is reported as short.
# Not the bound itself: a model aiming for 1,500 lands a little either side,
# and flagging 1,420자 would train the marketer to ignore the warning.
LENGTH_SHORT_RATIO = 0.8

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def measure_length(content: str, target=None) -> Dict:
    """Body length the way Naver's editor counts it (no spaces, no photo tags
    or source footer) plus the average sentence length — the two numbers the
    '너무 짧다' and '장황하다' complaints are actually about."""
    body = re.sub(r"\[IMAGE:[^\]]*\]", "", content or "")
    body = re.sub(r"\[원문 기사 출처:[^\]]*\]", "", body)
    chars = len(re.sub(r"\s", "", body))
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    avg = round(sum(len(s) for s in sentences) / len(sentences)) if sentences else 0
    out = {"chars": chars, "avg_sentence": avg, "target": list(target) if target else None}
    out["short"] = bool(target) and chars < target[0] * LENGTH_SHORT_RATIO
    return out


def _report(progress: Progress, message: str) -> None:
    # 이 print()는 순수 콘솔 디버그용이라 실패해도 무해해야 한다 — 그런데
    # Windows 콘솔의 기본 코드페이지(한국어 환경은 cp949)는 여기 메시지에
    # 흔히 섞이는 이모지(🔍·⚖️·📖 등)를 인코딩하지 못해 UnicodeEncodeError를
    # 던졌고, 그게 잡히지 않은 채 run_pipeline 전체를 그 자리에서 죽여서
    # progress(message)(=화면 진행 표시)가 호출조차 되지 못했다. 콘솔에
    # 못 찍는 것과 실제 생성 파이프라인이 실패하는 것은 전혀 다른 문제라
    # 여기서 확실히 분리한다.
    try:
        print(f"[content_writer] {message}")
    except UnicodeEncodeError:
        pass
    if progress:
        progress(message)


def _quality_pass(
    draft: str,
    final_title: str,
    target_keywords: List[str],
    skipped_keywords: List[str],
    brand_kit: dict,
    vendor: str,
    storage_file_paths: List[str],
    is_news: bool,
    source_url: Optional[str],
    progress: Progress,
    mode: Optional[dict] = None,
    notice_fields: Optional[dict] = None,
    product_fields: Optional[dict] = None,
    history: Optional[List[dict]] = None,
):
    """Stages 3~6c: compliance, SEO density, and the deterministic backstops.

    Shared by the from-scratch pipeline and `revise_content`, because a
    revision is fresh LLM output making the same brand claims — it needs the
    identical audit, not a lighter one. Returns (final_content, report).
    """
    profile = mode or content_mode.resolve(None, brand_kit)
    # --- stage 2d: search-intent coverage ---
    # Read-only: reports what a searcher for the target keyword expects and
    # the draft doesn't answer. Deliberately does not write the answers —
    # see search_intent's module docstring.
    #
    # Both read the same draft and neither feeds the other, so they run
    # concurrently — two sequential LLM round trips become one.
    _report(progress, "검색 의도 충족 점검 중…")
    with ThreadPoolExecutor(max_workers=1) as pool:
        intent_future = pool.submit(search_intent.run, final_title, draft, target_keywords, vendor)

        # --- stage 3: compliance guardrail ---
        if brand_kit.get("guardrail_enabled", True):
            _report(progress, "컴플라이언스 검수 중…")
        report = apply_guardrail_if_enabled(draft, brand_kit, vendor, notice_fields, product_fields)
        report["search_intent"] = intent_future.result()
    report["final_text"] = ensure_image_tags_preserved(draft, report["final_text"])

    # --- stage 4: SEO keyword density ---
    _report(progress, "네이버 SEO 키워드 밀도 확인 중…")
    final_content, density_report = rebalance_keywords(
        final_title, report["final_text"], target_keywords, vendor,
        minimum=profile.get("density_min", 2), maximum=profile.get("density_max", 6),
        enforce=profile.get("enforce_density", True),
    )
    report["seo_density"] = density_report

    # --- stage 4b: guard against SEO rebalancing undoing the guardrail ---
    # rebalance_keywords optimizes purely for keyword counts; if a target SEO
    # keyword happens to be a phrase the guardrail just rewrote out (e.g. the
    # admin's keyword list still has a greenwashing-flagged term), the LLM can
    # reinsert it verbatim while chasing density. Only re-run the guardrail —
    # one more LLM call — when that regression is actually detected.
    #
    # "Regressed" means gone after the guardrail and back after the SEO pass —
    # a flagged phrase the guardrail left in place (no replacement proposed)
    # is not something SEO undid, and re-auditing it would only repeat the
    # same finding for the price of another call.
    flagged_phrases = list(report.get("issue_phrases", {}).values())
    guarded_lower = report["final_text"].lower()
    regressed = [
        p for p in flagged_phrases
        if p and p.lower() not in guarded_lower and p.lower() in final_content.lower()
    ]
    if regressed:
        _report(progress, "SEO 보정이 되살린 컴플라이언스 문구 재검수 중…")
        reguard = apply_guardrail_if_enabled(final_content, brand_kit, vendor, notice_fields, product_fields)
        final_content = ensure_image_tags_preserved(final_content, reguard["final_text"])
        report["llm_issues"] = list(dict.fromkeys((report.get("llm_issues") or []) + reguard["llm_issues"]))
        report["issue_phrases"] = {**report.get("issue_phrases", {}), **reguard.get("issue_phrases", {})}
        report["seo_regression_fixed"] = regressed
        report["audit_failed"] = bool(report.get("audit_failed") or reguard.get("audit_failed"))

    # --- stage 4c: spelling / spacing ---
    # Runs on every generation, not just on revisions: the marketer's typed
    # edits reach here through revise_content, and the guardrail and SEO
    # rewrites above are themselves fresh LLM output that can introduce
    # errors. Placed before the deterministic backstops so those still have
    # the last word on image tags and banned terms.
    _report(progress, "맞춤법·오탈자 교정 중…")
    final_content, applied_fixes, rejected_fixes = proofread(final_content, brand_kit, vendor)
    report["proofread"] = {"applied": applied_fixes, "rejected": rejected_fixes}

    # --- stages 5~6: deterministic backstops ---
    final_content = ensure_image_tags_preserved(report["final_text"], final_content)
    final_content, _ = apply_blacklist_dictionary(final_content, brand_kit.get("blacklist_map", {}))
    final_content = ensure_all_photos_tagged(final_content, storage_file_paths)
    final_content = strip_unresolvable_image_tags(final_content, storage_file_paths)

    if is_news and source_url:
        # 뉴스 큐레이션에서 온 글은 원문 기사 링크가 본문에 반드시 있어야 합니다
        # (편집 방침) — 워크벤치에서 직접 쓴 글은 source_url 자체가 없어 이 블록을
        # 타지 않으므로 애초에 링크가 붙지 않습니다. 다른 재작성 단계를 전부
        # 거친 뒤 결정론적으로 붙입니다 — 각주가 LLM 재작성 여러 번을 [IMAGE:]
        # 태그처럼 버텨낼 필요가 없게 하기 위해서입니다.
        final_content = f"{final_content.rstrip()}\n\n[원문 기사 출처: {source_url}]"


    # --- stage 6b: resolve issues already fixed in the text that ships ---
    # `llm_issues` records what each guardrail pass found in *its own* input —
    # not what's still in the text after every later rewrite. A phrase whose
    # corrected_text already dropped it (or that stage 4b fixed) isn't a live
    # problem just because it was quoted earlier; checking against the text
    # that actually ships is what makes compliance_pass trustworthy.
    phrase_map = report.get("issue_phrases", {})
    still_open, resolved_issues = [], []
    for issue in report.get("llm_issues") or []:
        phrase = phrase_map.get(issue)
        if phrase and phrase.lower() not in final_content.lower():
            resolved_issues.append(issue)
        else:
            still_open.append(issue)
    # The certification-scope finding quotes a whole sentence, so any later
    # rewrite (SEO rebalance, proofread) changes the string and the check
    # above would call it resolved without the over-claim having gone. It is
    # deterministic and cheap, so re-run it on the text that actually ships
    # rather than trying to track the sentence through every pass.
    cert_issues = check_certification_scope(final_content, brand_kit)
    for finding in cert_issues:
        display = f"'{finding['phrase']}' — {finding['note']}"
        if display not in still_open:
            still_open.append(display)
        report.setdefault("issue_phrases", {})[display] = finding["phrase"]
    report["cert_scope_issues"] = [f["phrase"] for f in cert_issues]

    report["llm_issues"] = still_open
    report["resolved_issues"] = [i for i in resolved_issues if i not in still_open]
    report["compliance_pass"] = not still_open

    # --- stage 6c: re-measure density on the text that actually ships ---
    # Stage 4's density was measured on the rebalanced draft, before stage 4b's
    # re-audit and the deterministic passes above could strip words back out.
    report["seo_density"] = check_keyword_density(
        final_title, final_content, target_keywords,
        profile.get("density_min", 2), profile.get("density_max", 6),
    )
    if not profile.get("enforce_density", True):
        report["seo_density"]["enforced"] = False
    report["seo_targets"] = target_keywords
    report["seo_skipped"] = skipped_keywords
    report["seo_pool"] = list(brand_kit.get("seo_keywords") or [])
    report["content_mode"] = profile["key"]

    # A keyword the compliance guardrail is obliged to remove can never reach
    # its density target — regenerating just replays the same tug-of-war.
    stripped = {p.lower() for p in report.get("issue_phrases", {}).values() if p}
    stripped.update(p.lower() for p in report.get("seo_regression_fixed") or [])
    report["seo_conflicts"] = [
        kw for kw in report["seo_density"].get("missing") or []
        if any(kw.lower() in s or s in kw.lower() for s in stripped)
    ]

    # 공지 사실은 측정만 하고 고치지 않습니다 — 날짜와 금액을 LLM이 되살리게
    # 두는 것보다, 빠졌다고 알려주고 담당자가 편집기에서 넣는 편이 안전합니다.
    report["notice"] = factsheet.coverage(factsheet.NOTICE, final_content, notice_fields)
    report["product"] = factsheet.coverage(factsheet.PRODUCT, final_content, product_fields)

    # --- stage 6d: body similarity to recent posts (report only) ---
    # Measured on the text that ships, like everything above. `history` is
    # None when the caller didn't look it up, which reports as unchecked.
    report["body_variety"] = (
        body_variety.report(final_content, history) if history is not None else {"checked": False}
    )

    # --- stage 6e: body length vs the mode's target (report only) ---
    # Brief notices carry no target (see run_pipeline), so they are measured
    # but never called short.
    target = None
    if not factsheet.is_brief_overall(
        [(factsheet.NOTICE, notice_fields), (factsheet.PRODUCT, product_fields)]
    ):
        target = profile.get("length_range")
    report["length"] = measure_length(final_content, target)

    report["recommendation"] = _recommendation(report)
    return final_content, report


def run_pipeline(campaign_id: str, progress: Progress = None) -> Dict:
    """Generates all four channels for a campaign and persists the result.

    Raises on a failure that leaves nothing usable; the campaign row is
    flipped to 'failed' with the error in `publish_error` first, so the
    dashboard shows what happened instead of a silently stuck row.

    Raises repo.CampaignBusyError without touching the row if a run for this
    campaign is already in progress.
    """
    repo.begin_processing(campaign_id)
    try:
        vendor = get_configured_vendor()
        campaign = repo.get_campaign(campaign_id)
        if not campaign:
            raise RuntimeError(f"campaign {campaign_id} not found")

        brand_kit = repo.get_brand_kit()
        storage_file_paths = campaign.get("storage_file_paths") or []
        memo = (campaign.get("memo") or "").strip()
        given_title = (campaign.get("title") or "").strip()
        source_url = campaign.get("source_url")
        notice_fields = campaign.get("notice_fields") or {}
        product_fields = campaign.get("product_fields") or {}
        is_news = campaign.get("source_type") == "news"
        seo_keywords = brand_kit.get("seo_keywords") or []
        mode = content_mode.resolve(campaign.get("content_mode"), brand_kit)
        _report(progress, f"콘텐츠 모드: {mode['icon']} {mode['label']}")

        # --- stage 0: news article (news track only) ---
        article_text = ""
        source_title = (campaign.get("source_title") or "").strip()
        if is_news and source_url:
            _report(progress, "뉴스 기사를 읽는 중…")
            # Raises ArticleTooThin when the URL yields no usable body. That
            # propagates: run_pipeline's except clause writes it to
            # publish_error and flips the row to 'failed', which is the point
            # — a newsjacking post whose article never loaded used to finish
            # "successfully" as a generic brand post carrying a citation to
            # an article it had not read.
            article = scrape_article(source_url)
            article_text = article.body
            # Keyword-search results already carry the publisher's real title
            # from the RSS metadata; the scraped <title> for a Google News
            # link often lands on Google's redirect page instead.
            source_title = source_title or article.title
            # Cite (and remember) the outlet, not the aggregator. Persisting
            # it also keeps the news-search dedup working on the resolved URL.
            if article.url and article.url != source_url:
                repo.update_campaign(campaign_id, source_url=article.url)
                source_url = article.url
            # The headline is context for the writer, never the post's title.
            # Reusing it makes the post a 유사문서 in Naver's eyes (blog and
            # news share one duplicate-detection space), so it loses to the
            # original article — and it also skipped every title stage below,
            # since a pre-filled title means `needs_title` is False.

        # --- stage 1: photo captions (cached + batched) ---
        _report(progress, f"사진 {len(storage_file_paths)}장 분석 중…" if storage_file_paths else "첨부 사진 없음")
        captions = caption_attachments(storage_file_paths)

        # --- stage 2: the draft ---
        _report(progress, "블로그 본문 초안 작성 중…")
        needs_title = not given_title
        # Read from title_history, not from campaigns: the marketer prunes
        # campaigns down to a handful, and titles of deleted posts still need
        # to be avoided (core/db.py explains why they're stored separately).
        title_history = repo.recent_titles() if needs_title else []
        # Recent bodies: shown to the draft as "don't repeat these" and used
        # again after the quality pass to measure how far this one landed
        # from them — see ai_workers/body_variety.py.
        post_history = repo.recent_posts(exclude_id=campaign_id, limit=body_variety.HISTORY_LIMIT)
        seed_blocks = []
        if is_news and article_text:
            seed_blocks.append(f"[뉴스 원문]\n{article_text[:3000]}")
            if memo:
                seed_blocks.append(f"[담당자 메모 — 이 뉴스와 엮고 싶은 자사 맥락]\n{memo}")
            seed_blocks.append(
                "위 뉴스를 우리 브랜드의 관점에서 재해석한 네이버 블로그 포스트 본문을 작성하세요. "
                "뉴스를 그대로 요약하지 말고, 독자가 우리 제품/활동과 연결지어 읽을 수 있게 쓰되, "
                "기사의 구체적인 내용(무엇이 왜 화제인지)이 글에 분명히 남아 있어야 합니다. "
                + SUBJECT_INSTRUCTION
            )
        else:
            seed_blocks.append(f"[담당자가 작성한 메모 — 이 글의 소재]\n{memo}")
            seed_blocks.append(
                "위 메모를 자연스럽게 확장한 네이버 블로그 포스트 본문을 작성하세요. "
                + SUBJECT_INSTRUCTION
            )
        # 공지 정보는 뉴스/일반 어느 쪽이든 붙습니다 — 소재 설명 바로 뒤, 사진
        # 지시 앞에 와야 '이 글이 알려야 할 사실'로 읽힙니다.
        seed_blocks += notice_block(notice_fields, product_fields)

        prompt = "\n\n".join(
            seed_blocks
            + [
                f"[첨부 사진 및 설명 — 아래 목록의 사진만 사용 가능. 총 {len(captions)}장]\n"
                + photo_context(captions),
                photo_instruction(captions),
            ]
            + recent_posts_block(body_variety.prompt_history(post_history))
            + [
            ]
            + (
                [
                    _title_generation_instruction(
                        title_candidates(
                            seo_keywords,
                            brand_kit.get("keyword_weights"),
                            brand_kit.get("non_target_keywords"),
                        ),
                        source_title,
                    )
                    + avoidance_instruction(title_history)
                ]
                if needs_title
                else [f"[제목]\n{given_title}"]
            )
        )

        # 알릴 것이 적은 공지에서는 모드의 분량 목표를 걷어냅니다 —
        # factsheet.is_brief_overall 참고. 모드 선택 자체는 건드리지 않습니다: 키워드
        # 압력은 연휴 공지에도 그대로 적용되어야 합니다.
        draft_mode = mode
        if factsheet.is_brief_overall(
            [(factsheet.NOTICE, notice_fields), (factsheet.PRODUCT, product_fields)]
        ) and mode.get("length_range"):
            draft_mode = {**mode, "length_range": None}

        draft = generate_text(
            vendor=vendor,
            prompt=prompt,
            system=build_blog_system_prompt(brand_kit, draft_mode),
            # The ceiling moves with the length target. The target counts
            # characters without spaces; with spaces a Korean post is ~1.3x
            # that, and the tokenizers spend roughly one token per 1~1.5
            # characters, so 1.5 tokens per target character leaves headroom
            # for the title line and photo tags.
            max_tokens=max(3000, int((draft_mode.get("length_range") or (0, 0))[1] * 1.5) + 1000),
            note=f"blog-draft:{mode['key']}",
        )

        generated_title = None
        if needs_title:
            generated_title, draft = _extract_generated_title(draft)
        final_title = given_title or (generated_title or "").strip() or "제목 미정"

        # Checkpoint the paid-for draft before the audit/SEO stages, so an API
        # error in any of them (rate limit, outage) leaves the draft in the
        # editor instead of discarding the most expensive call of the run.
        # Body only — the title isn't saved here, because a saved title would
        # make the next run treat it as marketer-given and skip title
        # generation. The stale report is cleared so the unaudited body is
        # never shown next to a previous draft's '검수 통과'.
        repo.update_campaign(
            campaign_id, content=draft, guardrail_passed=None, guardrail_report=None
        )

        # --- stage 2a: pick this post's target keywords ---
        # Every later SEO stage measures against these, not the whole brand
        # pool — see select_target_keywords for why enforcing the full list
        # produced stuffed drafts.
        target_keywords = select_target_keywords(
            final_title, draft, seo_keywords, limit=mode["max_targets"],
            weights=brand_kit.get("keyword_weights"), min_mentions=mode["min_mentions"],
            non_targets=brand_kit.get("non_target_keywords"),
        )
        skipped_keywords = [kw for kw in seo_keywords if kw not in target_keywords]

        # --- stage 2b: title keyword verification ---
        # Only applies to AI-generated titles — a title the marketer typed
        # themselves is never rewritten out from under them (same rule
        # `rebalance_keywords` already follows for the body-density pass).
        title_missing_keywords: List[str] = []
        # Demoted keywords are targets for the body but never for the title —
        # see seo_optimizer.title_candidates. Without this the backstop puts
        # back exactly what the generation instruction was just stopped from
        # asking for.
        title_eligible = title_candidates(
            target_keywords,
            brand_kit.get("keyword_weights"),
            brand_kit.get("non_target_keywords"),
        )
        if needs_title and title_eligible and mode["rewrite_title"]:
            title_missing_keywords = title_keyword_coverage(final_title, title_eligible)
            if title_missing_keywords:
                _report(progress, "제목에 SEO 키워드 보강 중…")
                # History is passed so the candidate scorer can reject a
                # keyword-bearing title that repeats a past headline, instead
                # of leaving that entirely to the separate variety pass below.
                final_title = rewrite_title_for_keyword(
                    final_title, draft[:300], title_missing_keywords, vendor, title_history
                )

        # --- stage 2c: title variety against past titles ---
        # Runs after 2b because forcing a keyword in can itself push the title
        # back toward the shape of every other keyword-bearing title.
        title_variety = {"checked": bool(needs_title and title_history), "rewritten": False}
        if needs_title and title_history:
            # Brand name and target keywords are required to repeat, so they
            # are excluded from the comparison — see title_variety.py.
            ignore_terms = list(seo_keywords) + [brand_kit.get("brand_name") or ""]
            similar_to, score = closest_previous(final_title, title_history, ignore_terms)
            title_variety["score"] = round(score, 2)
            if similar_to and score >= SIMILARITY_THRESHOLD:
                _report(progress, "과거 제목과 유사해 제목을 다시 짓는 중…")
                candidate = rewrite_for_variety(
                    final_title, similar_to, title_missing_keywords or title_eligible[:1],
                    vendor, title_history,
                )
                # Keep the rewrite only if it still carries a target keyword —
                # variety must not cost the SEO coverage stage 2b just secured.
                # Measured against the title-eligible set, not every target: a
                # demoted keyword was never allowed in the title, so its
                # absence must not veto a rewrite.
                if not title_eligible or title_keyword_coverage(candidate, title_eligible) != title_eligible:
                    final_title = candidate
                    title_variety["rewritten"] = True
                    title_variety["similar_to"] = similar_to

        # --- stage 2d: the title gets the banned-term dictionary too ---
        # It never did. Every guardrail layer ran on the body only, so
        # "DDP 전시로 감상하는 더봄봄 한복 리사이클링의 미학" shipped with a
        # compliance-relevant term — 새활용(upcycling) mislabelled as
        # 리사이클링(recycling) — in the single most visible line of the post.
        # Deterministic substitution only: rewriting a title with an LLM here
        # would undo the keyword and variety work just done above.
        final_title, title_dict_hits = apply_blacklist_dictionary(
            final_title, brand_kit.get("blacklist_map", {})
        )

        final_content, report = _quality_pass(
            draft, final_title, target_keywords, skipped_keywords, brand_kit, vendor,
            storage_file_paths, is_news, source_url, progress, mode, notice_fields,
            product_fields, history=post_history,
        )
        report["title_dictionary_hits"] = title_dict_hits
        report["title_seo"] = {
            "checked": bool(needs_title and seo_keywords),
            "fixed": bool(needs_title and title_missing_keywords),
        }
        report["title_variety"] = title_variety

        # --- stage 7: secondary channels ---
        caption_values = list(captions.values())
        seed_note = memo or (article_text[:800] if article_text else final_title)

        # The four writers are independent of each other (each reads only the
        # memo/captions or the finished body), and each is 1~2 sequential LLM
        # round trips — run them concurrently so the slowest one, not the sum
        # of all four, sets the wait. _safe keeps each one best-effort.
        with ThreadPoolExecutor(max_workers=4) as pool:
            ig_future = pool.submit(
                _safe, progress, "인스타그램 캡션 생성 중…",
                lambda: _guarded_instagram(
                    seed_note, caption_values, brand_kit, vendor, notice_fields, product_fields
                ),
                {"caption": "", "hashtags": []},
            )
            x_future = pool.submit(
                _safe, progress, "X 스레드 생성 중…",
                lambda: _guarded_x(
                    seed_note, caption_values, brand_kit, vendor, notice_fields, product_fields
                ),
                {"tweets": [], "hashtags": []},
            )
            shorts_future = pool.submit(
                _safe, progress, "쇼츠 구성안 생성 중…",
                lambda: _guarded_shorts(
                    seed_note, caption_values, brand_kit, vendor, notice_fields, product_fields
                ),
                {"title": "", "hook": "", "scenes": [], "hashtags": []},
            )
            tags_future = pool.submit(
                _safe, progress, "네이버 발행 태그 생성 중…",
                lambda: write_naver_hashtags(final_title, final_content, brand_kit, vendor),
                [],
            )
            instagram = ig_future.result()
            x_result = x_future.result()
            shorts = shorts_future.result()
            naver_hashtags = tags_future.result()

        # --- stage 7b: platform format checks ---
        # The writers are only *told* about the 240-character tweet ceiling and
        # the 125-character Instagram fold. Verify it: an over-long tweet is
        # rejected by X outright, which is a harder failure than any SEO miss.
        _report(progress, "SNS 형식 검증 중…")
        x_result["tweets"], x_issues = validate_tweets(x_result["tweets"])
        instagram["caption"], instagram["hashtags"], ig_issues = validate_instagram(
            instagram["caption"], instagram["hashtags"]
        )
        report["sns_checks"] = {"x": x_issues, "instagram": ig_issues}
        # 형식 검증과는 별개입니다 — 이건 컴플라이언스 위반이 실제로 남아 있는지이고,
        # 위 sns_checks(글자수·해시태그 개수)와 섞이면 화면에서 사라지기 쉽습니다.
        # _guarded_instagram/_guarded_x가 감사는 이미 돌렸는데 결과를 버리고 있었고,
        # 그 결과 미보유 인증 확대 문장이 리포트에 아무 표시 없이 발행된 적이 있습니다.
        # shorts_script는 DB 컬럼 그대로 저장되므로, 리포트 전용인 compliance 키는
        # 저장 전에 떼어냅니다 — 인스타/X와 동일하게 캡션 자체와 감사 결과를 분리합니다.
        shorts_compliance = shorts.pop("compliance", None) or {"checked": False}
        report["sns_compliance"] = {
            "instagram": instagram.get("compliance") or {"checked": False},
            "x": x_result.get("compliance") or {"checked": False},
            "shorts": shorts_compliance,
        }

        repo.update_campaign(
            campaign_id,
            status="draft",
            title=final_title,
            source_title=source_title or None,
            content=final_content,
            guardrail_passed=report["compliance_pass"],
            guardrail_report=report,
            instagram_caption=instagram["caption"],
            instagram_hashtags=instagram["hashtags"],
            x_content=x_result["tweets"],
            x_hashtags=x_result["hashtags"],
            naver_hashtags=naver_hashtags,
            shorts_script=shorts,
        )
        # Outlives this campaign on purpose, so a future run still avoids this
        # title after the marketer prunes the campaign list (core/db.py).
        repo.record_title(final_title, campaign_id)
        _report(progress, "완료")
        return repo.get_campaign(campaign_id)

    except Exception as exc:
        repo.update_campaign(campaign_id, status="failed", publish_error=str(exc))
        raise


LENGTH_MODES = {
    "keep": {"label": "그대로", "ratio": None},
    "longer": {"label": "20% 길게", "ratio": 1.2},
    "shorter": {"label": "20% 짧게", "ratio": 0.8},
}

REVISION_SYSTEM_PROMPT = (
    "당신은 네이버 블로그 편집자입니다. 아래 본문을 담당자의 요청에 따라 고쳐 쓰세요. "
    "요청과 무관한 부분은 원문의 문장과 표현을 그대로 유지하고, 요청된 부분만 바꿉니다. "
    "새로운 사실이나 수치를 지어내지 마세요. "
    "`[IMAGE: 경로]` 형식의 태그는 사진이 삽입될 위치 마크업이므로 삭제·수정하지 말고, "
    "개수와 순서를 그대로 유지한 채 문맥에 맞는 자리에 남겨두세요. "
    "고쳐 쓴 본문 전체만 출력하고 다른 설명은 절대 덧붙이지 마세요."
)


def revise_content(
    campaign_id: str,
    instruction: str = "",
    length_mode: str = "keep",
    progress: Progress = None,
) -> Dict:
    """Rewrites the *current* body — including the marketer's manual edits —
    instead of regenerating from the memo the way `run_pipeline` does.

    `run_pipeline` always starts from the memo, so it throws away whatever the
    marketer typed into the editor. That makes it the wrong tool for "make
    this bit warmer" or "trim it a bit", which is most of the real editing
    loop. The revision output is fresh LLM text making brand claims, so it
    goes through the same `_quality_pass` audit rather than a lighter one.

    Instagram/X/쇼츠 are deliberately not regenerated: they are written from
    the memo and photo captions, not from this body, so re-running them would
    spend four LLM calls to produce the same text. Naver tags are refreshed
    because they are derived from the body.
    """
    campaign = repo.get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError(f"campaign {campaign_id} not found")

    base_content = (campaign.get("content") or "").strip()
    if not base_content:
        raise RuntimeError("수정할 본문이 없습니다. 먼저 초안을 생성하세요.")

    instruction = (instruction or "").strip()
    ratio = LENGTH_MODES.get(length_mode, LENGTH_MODES["keep"])["ratio"]

    previous_status = repo.begin_processing(campaign_id)
    try:
        vendor = get_configured_vendor()
        brand_kit = repo.get_brand_kit()
        seo_keywords = brand_kit.get("seo_keywords") or []
        final_title = (campaign.get("title") or "").strip() or "제목 미정"
        storage_file_paths = campaign.get("storage_file_paths") or []

        requests = []
        if instruction:
            requests.append(f"[담당자 수정 요청]\n{instruction}")
        if ratio:
            # An explicit character target is far more reliable than "조금 더
            # 길게" — the model has no sense of the current length otherwise.
            target = int(len(base_content) * ratio)
            direction = "늘려" if ratio > 1 else "줄여"
            requests.append(
                f"[분량 조정]\n현재 본문은 약 {len(base_content)}자입니다. "
                f"내용을 {direction} 약 {target}자 내외로 다시 쓰세요. "
                + (
                    "새로운 사실을 지어내지 말고, 기존 내용의 묘사와 설명을 더 구체적으로 풀어 쓰세요."
                    if ratio > 1
                    else "핵심 메시지와 사진 태그는 유지하고 군더더기 표현을 덜어내세요."
                )
            )

        # 줄여 쓰기 요청은 군더더기부터 덜어내는데, 공지·제품 글에서 가장
        # 군더더기처럼 보이는 줄이 정작 일시·가격·주문 방법입니다. 수정 호출이
        # 실제로 일어날 때만 붙입니다.
        if requests:
            kept_lines = []
            for sheet, given in (
                (factsheet.NOTICE, campaign.get("notice_fields")),
                (factsheet.PRODUCT, campaign.get("product_fields")),
            ):
                kept_lines += [
                    f"- {sheet.labels[k]}: {v}"
                    for k, v in factsheet.clean(sheet, given).items()
                ]
            if kept_lines:
                requests.append(
                    "[핵심 정보 유지]\n다음 사실은 이 글의 존재 이유이므로 어떤 수정 "
                    "요청에도 본문에서 빼거나 바꾸지 마세요:\n" + "\n".join(kept_lines)
                )

        if requests:
            _report(progress, "수정 요청 반영 중…")
            revised = generate_text(
                vendor=vendor,
                prompt=f"[현재 본문]\n{base_content}\n\n" + "\n\n".join(requests),
                system=REVISION_SYSTEM_PROMPT,
                # Headroom for the longer case: Korean runs ~2 characters/token.
                max_tokens=max(2500, int(len(base_content) * (ratio or 1) / 2) + 800),
                note=f"revision:{length_mode}",
            )
            revised = ensure_image_tags_preserved(base_content, revised.strip() or base_content)
        else:
            # No instruction and no length change: the marketer just wants
            # their edits checked. _quality_pass still proofreads and re-audits,
            # so skipping the rewrite call costs nothing but tokens saved.
            revised = base_content

        # Re-select targets from the revised text: a length change can drop a
        # keyword the previous target set relied on.
        mode = content_mode.resolve(campaign.get("content_mode"), brand_kit)
        target_keywords = select_target_keywords(
            final_title, revised, seo_keywords, limit=mode["max_targets"],
            weights=brand_kit.get("keyword_weights"), min_mentions=mode["min_mentions"],
            non_targets=brand_kit.get("non_target_keywords"),
        )
        skipped_keywords = [kw for kw in seo_keywords if kw not in target_keywords]

        final_content, report = _quality_pass(
            revised, final_title, target_keywords, skipped_keywords, brand_kit, vendor,
            storage_file_paths, campaign.get("source_type") == "news",
            campaign.get("source_url"), progress, mode,
            campaign.get("notice_fields") or {}, campaign.get("product_fields") or {},
            history=repo.recent_posts(exclude_id=campaign_id, limit=body_variety.HISTORY_LIMIT),
        )
        report["revision"] = {
            "instruction": instruction,
            "length_mode": length_mode,
            "before_chars": len(base_content),
            "after_chars": len(final_content),
        }

        naver_hashtags = _safe(
            progress, "네이버 발행 태그 갱신 중…",
            lambda: write_naver_hashtags(final_title, final_content, brand_kit, vendor),
            campaign.get("naver_hashtags") or [],
        )

        repo.update_campaign(
            campaign_id,
            status="draft",
            content=final_content,
            guardrail_passed=report["compliance_pass"],
            guardrail_report=report,
            naver_hashtags=naver_hashtags,
        )
        _report(progress, "완료")
        return repo.get_campaign(campaign_id)

    except Exception as exc:
        # A failed *revision* leaves the existing body untouched, so the
        # campaign goes back to what it was (e.g. a finished draft) rather
        # than being demoted to 'failed' — which would hide a perfectly good
        # draft behind an error state over a transient API hiccup. The error
        # is still recorded and re-raised for the screen to show.
        repo.update_campaign(
            campaign_id,
            status=previous_status if previous_status != "processing" else "draft",
            publish_error=str(exc),
        )
        raise


def _safe(progress: Progress, message: str, fn: Callable, fallback):
    """Secondary channels are best-effort: the Naver draft is the primary
    deliverable and a caption failure must never discard it."""
    _report(progress, message)
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[content_writer] {message} failed: {exc}")
        return fallback


def _compliance_summary(guarded: dict) -> dict:
    """The subset of a guardrail result worth showing the marketer for a
    secondary channel — same shape for Instagram and X so the workbench can
    render both with one code path.

    Extracted because the caller used to keep only `final_text` and throw the
    rest away. The deterministic dictionary substitution still landed (it's
    baked into `final_text` either way), but anything the *LLM audit* only
    flagged rather than auto-corrected — including the certification-scope
    check, which by design never rewrites text, only detects — vanished
    without a trace. A live 미보유 인증 확대 sentence ('더봄봄의 주요 제품들은
    … 인증을 받아') shipped in an Instagram caption during testing with the
    report showing nothing wrong, because nothing downstream of this function
    ever looked at `compliance_pass` or `llm_issues` for these two channels.

    `llm_issues` (`grounded_issues` inside guardrail.py) is checked against
    the *pre-correction* draft the audit read, not the corrected text it
    returned — a finding is "grounded" simply because the model found it
    somewhere in what it was given, which is trivially almost always true.
    The Naver body has a dedicated re-check (`_quality_pass` stage 6b) that
    reopens or resolves each finding against the text that actually ships;
    without the same re-check here, a caption where the audit itself already
    fixed '10년 동안' still reported "컴플라이언스 미해결" for a phrase that
    was no longer anywhere in what got saved. Re-running that same check
    against `guarded["final_text"]` here closes the gap.
    """
    phrase_map = guarded.get("issue_phrases") or {}
    shipped = (guarded.get("final_text") or "").lower()
    still_open = [
        issue for issue in (guarded.get("llm_issues") or [])
        # 인용 문구가 없으면 검증할 수 없으니(설계상 늘 그렇듯) 미해결로 둡니다.
        if not phrase_map.get(issue) or phrase_map[issue].lower() in shipped
    ]
    return {
        "checked": guarded.get("compliance_pass") is not None,
        "compliance_pass": (not still_open) if guarded.get("compliance_pass") is not None else None,
        "score": guarded.get("score"),
        "issues": still_open,
        "dictionary_hits": list(guarded.get("dictionary_hits") or []),
        "auto_removed": list(guarded.get("auto_removed") or []),
        "tags_removed": list(guarded.get("tags_removed") or []),
    }


def _finalize_sns(guarded: dict, texts: List[str], hashtags: List[str], brand_kit: dict):
    """Deterministic certification clean-up for a short SNS post, after the
    audit. Returns (texts, hashtags) and records what it did on `guarded`.

    * Sentences that stretch a certification over the whole catalogue are
      deleted (guardrail.remove_certification_overclaims) — in a caption they
      are removable filler, and left in place they kept the post '미해결'.
    * Certification hashtags are dropped when no certification statement is
      left in the text (guardrail.filter_certification_hashtags).

    `guarded["final_text"]` is reset to the text that actually ships, so the
    resolution check in _compliance_summary sees the deletions and closes
    the findings they fixed. Skipped when the guardrail is switched off.
    """
    if not brand_kit.get("guardrail_enabled", True):
        return texts, hashtags
    cleaned, removed = [], []
    for text in texts:
        text, gone = remove_certification_overclaims(text or "", brand_kit)
        cleaned.append(text)
        removed += gone
    kept_tags, dropped_tags = filter_certification_hashtags(hashtags, "\n".join(cleaned), brand_kit)
    guarded["final_text"] = "\n".join(cleaned)
    guarded["auto_removed"] = removed
    guarded["tags_removed"] = dropped_tags
    return cleaned, kept_tags


def _guarded_instagram(
    note: str, caption_values: List[str], brand_kit: dict, vendor: str,
    notice_fields: Optional[dict] = None, product_fields: Optional[dict] = None,
) -> dict:
    """The caption goes through the same compliance guardrail as the Naver
    body — it's separately generated text making its own claims about the
    brand, not a derivative of the audited body, so skipping it would leave a
    real compliance gap."""
    result = write_instagram_caption(note, caption_values, brand_kit, vendor)
    guarded = apply_guardrail_if_enabled(
        result["caption"], brand_kit, vendor, notice_fields, product_fields
    )
    (result["caption"],), result["hashtags"] = _finalize_sns(
        guarded, [guarded["final_text"]], result.get("hashtags") or [], brand_kit
    )
    result["compliance"] = _compliance_summary(guarded)
    return result


_TWEET_DELIMITER = "\n<<<TWEET>>>\n"


def _guarded_x(
    note: str, caption_values: List[str], brand_kit: dict, vendor: str,
    notice_fields: Optional[dict] = None, product_fields: Optional[dict] = None,
) -> dict:
    """Same reasoning as the Instagram block: X gets its own generated text
    making brand claims, so it gets the same audit.

    The whole thread is audited in **one** call rather than one per tweet.
    The audit system prompt is ~500 tokens, so a 5-tweet thread was paying
    2,500 tokens of instructions to review maybe 300 tokens of content — and
    a tweet reviewed in isolation is also worse-informed than one reviewed
    alongside the rest of its thread. If the model doesn't return the
    delimiter structure intact, fall back to the deterministic dictionary
    pass: the hard guarantee (no banned term survives) is preserved either
    way, and a mangled thread is worse than an unaudited-by-LLM one.
    """
    result = write_x_thread(note, caption_values, brand_kit, vendor)
    tweets = result["tweets"]
    if not tweets or not brand_kit.get("guardrail_enabled", True):
        result["compliance"] = _compliance_summary({})
        return result

    joined = _TWEET_DELIMITER.join(tweets)
    guarded = apply_guardrail_if_enabled(joined, brand_kit, vendor, notice_fields, product_fields)
    guarded_text = guarded["final_text"]
    parts = [p.strip() for p in guarded_text.split(_TWEET_DELIMITER.strip())]
    parts = [p for p in parts if p]

    if len(parts) == len(tweets):
        result["tweets"] = parts
    else:
        print(
            f"[content_writer] X guardrail returned {len(parts)} segments for {len(tweets)} "
            "tweets — keeping originals with dictionary substitution only."
        )
        result["tweets"] = [
            apply_blacklist_dictionary(t, brand_kit.get("blacklist_map", {}))[0] for t in tweets
        ]
    # Per tweet, after the split, so a deletion can never shift the delimiter
    # alignment; a tweet that was nothing but the over-claim is dropped.
    tweets_out, result["hashtags"] = _finalize_sns(
        guarded, result["tweets"], result.get("hashtags") or [], brand_kit
    )
    result["tweets"] = [t for t in tweets_out if t.strip()]
    result["compliance"] = _compliance_summary(guarded)
    return result


_SHORTS_DELIMITER = "\n<<<SEG>>>\n"


def _guarded_shorts(
    note: str, caption_values: List[str], brand_kit: dict, vendor: str,
    notice_fields: Optional[dict] = None, product_fields: Optional[dict] = None,
) -> dict:
    """Same audit as Instagram/X, applied last — this channel had none at all.

    Only the title, hook and on-screen captions are audited: those are the
    words a viewer reads, and the only place a greenwashing or over-broad
    certification claim could actually land. `shot`(촬영 지시) is a filming
    instruction the audience never sees, so it is not brand copy and is left
    out of both the audit and the segment count.

    Same delimiter-split-and-realign approach as `_guarded_x`, for the same
    reason: one audit call for the whole script instead of one per line, and
    a fallback to dictionary-only substitution if the model doesn't return
    the segments intact.
    """
    result = write_shorts_script(note, caption_values, brand_kit, vendor)
    scenes = result.get("scenes") or []
    segments = [result.get("title", ""), result.get("hook", "")] + [
        s.get("caption", "") for s in scenes
    ]
    if not any(seg.strip() for seg in segments) or not brand_kit.get("guardrail_enabled", True):
        result["compliance"] = _compliance_summary({})
        return result

    joined = _SHORTS_DELIMITER.join(segments)
    guarded = apply_guardrail_if_enabled(joined, brand_kit, vendor, notice_fields, product_fields)
    parts = [p.strip() for p in guarded["final_text"].split(_SHORTS_DELIMITER.strip())]

    if len(parts) == len(segments):
        result["title"], result["hook"], *cut_captions = parts
        for scene, caption in zip(scenes, cut_captions):
            scene["caption"] = caption
    else:
        print(
            f"[content_writer] Shorts guardrail returned {len(parts)} segments for "
            f"{len(segments)} — keeping originals with dictionary substitution only."
        )
        blacklist = brand_kit.get("blacklist_map", {})
        result["title"] = apply_blacklist_dictionary(result.get("title", ""), blacklist)[0]
        result["hook"] = apply_blacklist_dictionary(result.get("hook", ""), blacklist)[0]
        for scene in scenes:
            scene["caption"] = apply_blacklist_dictionary(scene.get("caption", ""), blacklist)[0]
    # Per segment, same reason as _guarded_x.
    texts = [result.get("title", ""), result.get("hook", "")] + [s.get("caption", "") for s in scenes]
    texts, result["hashtags"] = _finalize_sns(guarded, texts, result.get("hashtags") or [], brand_kit)
    result["title"], result["hook"], *cut_captions = texts
    for scene, caption in zip(scenes, cut_captions):
        scene["caption"] = caption
    result["compliance"] = _compliance_summary(guarded)
    return result
