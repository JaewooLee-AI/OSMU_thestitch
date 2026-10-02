"""교육·공지 글에 답례품이 끼어들던 문제와 '서술형 나열' 문제의 회귀 테스트.

LLM을 부르지 않습니다 — 초안 호출에 실제로 들어가는 프롬프트와, 그 결과를
네이버로 보내는 변환을 검사합니다.
"""
from ai_workers import body_format, content_mode, post_type, prompt_builder
from ai_workers.content_writer import _title_generation_instruction, measure_length
from core import brand_seed

# 고객사 실제 풀과 같은 모양: 답례품·제작 위주, 교육 키워드 없음.
GIFT_POOL = ["결혼답례품", "돌답례품", "칠순답례품", "굿즈제작", "키링제작", "더봄봄"]


def _brand_kit():
    return {
        "brand_name": brand_seed.BRAND_NAME, "sub_brand": brand_seed.SUB_BRAND,
        "industry": brand_seed.INDUSTRY, "persona": brand_seed.PERSONA,
        "tone_and_manner": brand_seed.TONE_AND_MANNER, "core_facts": brand_seed.CORE_FACTS,
        "terminology": brand_seed.TERMINOLOGY, "few_shot_samples": brand_seed.FEW_SHOT_SAMPLES,
        "seo_keywords": GIFT_POOL, "non_target_keywords": ["더봄봄"],
    }


# --- 글 유형 자동 판단 ---------------------------------------------------------

def test_guess_education_from_memo():
    assert post_type.guess("한복 새활용 강사과정 2기 모집합니다") == "education"
    assert post_type.guess("어르신 대상 행복인형 만들기 체험 수업") == "education"


def test_guess_notice_event_story_product():
    assert post_type.guess("추석 연휴 안내", {"when": "9월 24일부터 27일까지 휴무"}) == "notice"
    assert post_type.guess("DDP 서울패션마켓 팝업 참가") == "event"
    assert post_type.guess("", is_news=True) == "story"
    assert post_type.guess("행복인형 미니미 노리개키링 신제품") == "product"


def test_product_sheet_wins():
    assert post_type.guess("체험 키트", product_fields={"price": "1만원"}) == "product"


def test_pinned_type_overrides_guess():
    profile = post_type.resolve("product", "강사과정 모집")
    assert profile["key"] == "product" and not profile["auto"]
    assert post_type.resolve("auto", "강사과정 모집")["auto"]


# --- 키워드 압력 제한 ---------------------------------------------------------

def test_education_behaves_as_content_first():
    seo = content_mode.resolve("seo")
    narrowed = post_type.apply_to_mode(seo, post_type.resolve("education"))
    assert narrowed["hint"] == content_mode.HINT_NONE
    assert narrowed["enforce_density"] is False and narrowed["rewrite_title"] is False
    assert narrowed["length_range"] == seo["length_range"]  # 교육 글은 분량은 유지
    # 제품 글은 모드 그대로
    assert post_type.apply_to_mode(seo, post_type.resolve("product"))["hint"] == content_mode.HINT_PLACEMENT


def test_notice_gets_no_keywords_and_no_length_target():
    narrowed = post_type.apply_to_mode(content_mode.resolve("seo"), post_type.resolve("notice"))
    assert narrowed["hint"] == content_mode.HINT_NONE
    assert narrowed["enforce_density"] is False
    assert narrowed["rewrite_title"] is False
    assert narrowed["length_range"] is None


def test_type_never_strengthens_mode():
    rich = content_mode.resolve("rich")
    assert post_type.apply_to_mode(rich, post_type.resolve("product"))["hint"] == content_mode.HINT_NONE


# --- 제목 지시 (가장 큰 원인이었던 버그) ---------------------------------------

def _title_rule(mode_key, type_key):
    mode = post_type.apply_to_mode(content_mode.resolve(mode_key), post_type.resolve(type_key))
    return post_type.title_keyword_rule(mode, post_type.resolve(type_key))


def test_title_rule_per_mode_and_type():
    assert _title_rule("rich", "product") == "none"
    assert _title_rule("seo", "product") == "required"
    assert _title_rule("balanced", "product") == "optional"
    assert _title_rule("seo", "education") == "none"
    assert _title_rule("seo", "event") == "optional"
    assert _title_rule("seo", "notice") == "none"


def test_title_instruction_does_not_force_gift_keyword_on_education():
    text = _title_generation_instruction(GIFT_POOL, "", _title_rule("seo", "education"))
    assert "답례품" not in text and "키링" not in text


def test_title_instruction_event_offers_keywords_only_if_relevant():
    text = _title_generation_instruction(GIFT_POOL, "", _title_rule("seo", "event"))
    assert "최소 1개" not in text
    assert "실제로 맞는 것이 있을 때만" in text


def test_title_instruction_rich_mode_shows_no_keywords():
    text = _title_generation_instruction(GIFT_POOL, "", _title_rule("rich", "education"))
    assert "답례품" not in text


# --- 초안 시스템 프롬프트 -----------------------------------------------------

def _system_prompt(mode_key, type_key):
    ptype = post_type.resolve(type_key)
    mode = post_type.apply_to_mode(content_mode.resolve(mode_key), ptype)
    return prompt_builder.build_blog_system_prompt(_brand_kit(), mode, ptype)


def test_education_prompt_has_no_keyword_pool_and_guards_topic():
    prompt = _system_prompt("seo", "education")
    assert "각각 2~3회" not in prompt
    assert "결혼답례품" not in prompt and "키링제작" not in prompt
    assert "답례품 홍보로 넘어가지 마세요" in prompt
    assert "무엇을 만들고 무엇을 배우는지" in prompt


def test_notice_prompt_shows_no_keyword_pool():
    prompt = _system_prompt("seo", "notice")
    assert "결혼답례품" not in prompt
    assert "돌답례품" not in prompt


def test_product_seo_prompt_keeps_placement():
    prompt = _system_prompt("seo", "product")
    assert "각각 2~3회" in prompt
    assert "한 문단에는 같은 키워드를 한 번만" in prompt


def test_prompt_asks_for_structure_not_paragraph_only():
    prompt = _system_prompt("balanced", "product")
    assert "문단으로만" not in prompt
    assert "'■ '로" in prompt and "'• '로" in prompt


# --- 본문 서식 ----------------------------------------------------------------

def test_normalize_markdown_to_markers():
    raw = "## 수업 소개\n**준비물**\n- 가위\n* 실\n멈추오니,, 연락.. 그리고... 끝\n2026. 10. 3."
    assert body_format.normalize(raw) == (
        "■ 수업 소개\n■ 준비물\n• 가위\n• 실\n멈추오니, 연락. 그리고... 끝\n2026. 10. 3."
    )


def test_normalize_is_idempotent():
    once = body_format.normalize("## 제목\n- 항목\n본문 **강조** 문장")
    assert body_format.normalize(once) == once


def test_to_html_bolds_headings_and_escapes():
    html = body_format.to_html("첫 문단 A&B\n■ 소제목\n• 항목")
    assert html == (
        "<p>첫 문단 A&amp;B</p><p><br></p><p><b>■ 소제목</b></p><p>• 항목</p>"
    )


def test_length_ignores_markers():
    plain = measure_length("가나다라\n마바사")
    marked = measure_length("■ 가나다라\n• 마바사")
    assert plain["chars"] == marked["chars"]
    assert marked["headings"] == 1


# --- 긴 문장 나누기 (LLM 응답은 가짜로 대체) ------------------------------------

def test_long_sentences_skip_headings_and_lists():
    from ai_workers import sentence_length
    long = "가" * 70 + "."
    text = f"■ {'나' * 70}\n• {'다' * 70}\n{long} 짧은 문장."
    assert sentence_length.long_sentences(text) == [long]


def test_shorten_applies_only_valid_rewrites(monkeypatch):
    from ai_workers import sentence_length
    good = "기부받은 한복 원단으로 만든 행복인형 미니미는 약 10cm 크기라서 가방이나 차 키에 가볍게 달기 좋은 노리개키링입니다."
    bad = "수업은 2026년 10월 20일부터 매주 화요일 오전 10시에 시작해서 총 6회 동안 작업장에서 차근차근 진행됩니다."
    reply = {
        "rewrites": [
            {"before": good, "after": "기부받은 한복 원단으로 만든 행복인형 미니미예요. 약 10cm 크기라 가방이나 차 키에 달기 좋은 노리개키링입니다."},
            # 숫자가 바뀐 분리는 거부되어야 합니다.
            {"before": bad, "after": "수업은 10월 21일에 시작합니다. 매주 화요일 오전 10시, 총 6회 진행됩니다."},
        ]
    }
    import json
    monkeypatch.setattr(sentence_length, "generate_text", lambda **kw: json.dumps(reply, ensure_ascii=False))
    out, report = sentence_length.shorten(f"{good}\n{bad}", "google", ["행복인형 미니미", "노리개키링"])
    assert "약 10cm 크기라" in out
    assert bad in out
    assert len(report["applied"]) == 1
    assert report["rejected"][0]["reason"] == "숫자 변경"


def test_shorten_rejects_dropping_keyword(monkeypatch):
    from ai_workers import sentence_length
    import json
    before = "결혼답례품으로 준비하신다면 색동 원단의 고운 빛깔과 손바느질의 정갈한 멋이 하객분들께 오래도록 기억에 남는 선물이 되어 줄 거예요."
    reply = {"rewrites": [{"before": before, "after": "하객 선물로 좋아요. 색동 빛깔이 오래 기억에 남습니다."}]}
    monkeypatch.setattr(sentence_length, "generate_text", lambda **kw: json.dumps(reply, ensure_ascii=False))
    out, report = sentence_length.shorten(before, "google", ["결혼답례품"])
    assert out == before
    assert "누락" in report["rejected"][0]["reason"]


# --- 반복 표현 ----------------------------------------------------------------

def test_stock_phrases_found_and_brand_terms_spared():
    from ai_workers import body_variety
    posts = [
        {"content": f"가장 행복한 날 입었던 한복으로 만들었어요. 더봄봄 행복인형 {i}번입니다. 정성을 다해 만들 수 있습니다."}
        for i in range(4)
    ]
    phrases = body_variety.stock_phrases(posts, ["더봄봄", "행복인형"])
    assert "가장 행복한 날 입었던" in phrases
    assert not any("더봄봄" in p or "행복인형" in p for p in phrases)
    assert "수 있습니다" not in phrases


def test_stock_phrases_need_enough_history():
    from ai_workers import body_variety
    assert body_variety.stock_phrases([{"content": "같은 말 같은 말 반복"}] * 2) == []


def test_no_targets_warning_suppressed_for_keyword_free_types():
    from ai_workers.content_writer import _recommendation
    base = {"seo_targets": [], "seo_pool": ["결혼답례품"], "content_mode": "seo"}
    assert _recommendation(base)["no_targets"] is True
    edu = {**base, "post_type": {"key": "education", "keyword_free": True}}
    assert _recommendation(edu)["no_targets"] is False


# --- 자료에 없는 숫자·인용 ----------------------------------------------------

def test_unsupported_numbers_and_quotes():
    from ai_workers import unsupported
    memo = "고등학교 특수학급 학생 8명과 계피 향낭을 만들었습니다. 가족 선물로 가져가겠다는 학생이 많았어요."
    facts = ["아동미술 1,000회, 어르신미술 500회 이상의 교육을 진행해 왔습니다."]
    body = (
        "학생 8명과 함께했습니다. 소요 시간: 2교시 블록타임 (총 90분)\n"
        "아동미술 1,000회의 경험이 있습니다.\n"
        "\"추석에 시골 갈 때 할머니 베갯맡에 꼭 놓아드릴 거예요.\"\n"
        "[IMAGE: 2026/10/abc123.jpg]"
    )
    found = unsupported.find(body, [memo] + facts)
    assert "90분" in found["numbers"] and "2교시" in found["numbers"]
    assert not any(n.startswith("8명") or n.startswith("1,000") for n in found["numbers"])
    assert found["quotes"] and found["quotes"][0].startswith("추석에")


def test_quote_from_memo_is_supported():
    from ai_workers import unsupported
    memo = '학생이 "계피 향이 너무 좋아요"라고 했어요.'
    assert unsupported.find('"계피 향이 너무 좋아요"', [memo])["quotes"] == []


def test_product_length_target():
    product = post_type.resolve("product")
    for key in ("balanced", "seo"):
        assert post_type.apply_to_mode(content_mode.resolve(key), product)["length_range"] == (1200, 1800)
    rich = content_mode.resolve("rich")
    assert post_type.apply_to_mode(rich, product)["length_range"] == rich["length_range"]
    # 다른 유형은 모드의 목표 그대로
    assert post_type.apply_to_mode(content_mode.resolve("seo"), post_type.resolve("event"))["length_range"] == (1500, 2000)
