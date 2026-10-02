"""직접 쓴 글 진단 — 네트워크 없이 확인할 수 있는 부분."""
from ai_workers import post_audit


def test_title_candidates_pairs_do_not_cross_punctuation():
    cands = post_audit.title_keyword_candidates("명절 선물, 기념품,외국인 선물... 고민 끝!! 아리랑 색동핀 추천")
    assert "명절선물" in cands and "외국인선물" in cands and "아리랑색동핀" in cands
    assert "선물기념품" not in cands          # 쉼표를 건너 붙이지 않음
    assert not any("추천" in c or "고민" in c for c in cands)


def test_verdicts():
    assert post_audit._verdict(15, 1000.0) == "no_demand"
    assert post_audit._verdict(79_600, 31.4) == "head"     # 명절선물: 대형 검색어
    assert post_audit._verdict(1_350, 6.7) == "easy"       # 노리개키링
    assert post_audit._verdict(790, 50.3) == "stretch"     # 한복키링
    assert post_audit._verdict(11_300, 744.7) == "crowded" # 기념품


def test_text_checks_and_advice_without_api():
    kit = {"blacklist_map": {"세상에 하나뿐인": "저마다 다른"}, "seo_keywords": ["노리개키링"]}
    body = "작은 한복 조각 하나하나가\n세상에 하나뿐인 미니미가 됩니다.\n노리개키링으로 가방에 달아보세요.\n구매링크 http://x.y/1"
    report = post_audit.audit("한복새활용 행복인형 미니미 오픈!!", body, photo_count=15, brand_kit=kit, use_api=False)
    t = report["text"]
    assert t["banned_terms"] == [{"term": "세상에 하나뿐인", "replacement": "저마다 다른"}]
    assert "가격" in t["purchase_missing"]
    assert t["chars_per_photo"] < post_audit.MIN_CHARS_PER_PHOTO
    assert report["pool_in_body_not_title"] == ["노리개키링"]
    advice = " ".join(report["advice"])
    assert "사진 15장" in advice and "세상에 하나뿐인" in advice and "'!!'" in advice


def test_view_builds_and_renders():
    from flet_app.state import AppState
    from flet_app.views import post_audit_view
    post_audit_view.build(None, AppState())
    report = post_audit.audit("제목", "본문입니다.", 1, brand_kit={}, use_api=False)
    report["title_keywords"] = [{"keyword": "노리개키링", "volume": 1350, "documents": 9070, "ratio": 6.7, "verdict": "easy"}]
    assert post_audit_view._render(report, 1.0)
