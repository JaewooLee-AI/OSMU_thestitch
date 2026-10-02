"""사진 설명 — 생성 결과 검증과 네이버 입력 규칙."""
import json


def test_write_captions_keeps_existing_and_validates(monkeypatch):
    from ai_workers import photo_captions
    content = "도입\n[IMAGE: a.jpg]\n키링 설명\n[IMAGE: b.jpg]\n끝"
    reply = {"captions": [
        {"photo": 1, "caption": "세상에 하나뿐인 색동 노리개키링."},
        {"photo": 9, "caption": "없는 사진"},
    ]}
    calls = []

    def fake(**kw):
        calls.append(kw["prompt"])
        return json.dumps(reply, ensure_ascii=False)

    monkeypatch.setattr(photo_captions, "generate_text", fake)
    out = photo_captions.write_captions(
        "제목", content, ["a.jpg", "b.jpg", "c.jpg"], {"b.jpg": "인형", "c.jpg": "포장"},
        {"blacklist_map": {"세상에 하나뿐인": "저마다 다른"}}, "google",
        existing={"a.jpg": "담당자가 고친 설명"},
    )
    assert out["a.jpg"] == "담당자가 고친 설명"            # 기존 설명 유지
    assert out["b.jpg"] == "저마다 다른 색동 노리개키링"     # 금기어 치환, 마침표 제거
    assert "c.jpg" not in out                              # 범위 밖 번호는 버림
    assert "a.jpg" not in calls[0].split("[사진 목록]")[1]   # 이미 있는 사진은 다시 묻지 않음


def test_no_call_when_all_captioned(monkeypatch):
    from ai_workers import photo_captions
    monkeypatch.setattr(photo_captions, "generate_text", lambda **kw: (_ for _ in ()).throw(AssertionError))
    out = photo_captions.write_captions("t", "[IMAGE: a.jpg]", ["a.jpg"], {}, {}, "google", existing={"a.jpg": "x"})
    assert out == {"a.jpg": "x"}


class _Slot:
    def __init__(self, page):
        self.page, self.text = page, ""
    def evaluate(self, js): self.page.focused = self    # scroll / caret script
    def bounding_box(self): return {"width": 100, "height": 20}
    def click(self, force=False): self.page.focused = self
    def inner_text(self): return self.text


class _Component:
    """A photo component whose caption only exists after the photo is clicked."""
    def __init__(self, page, hidden_until_click=False):
        self.slot, self.revealed = _Slot(page), not hidden_until_click
    def query_selector(self, sel):
        return self if sel == "img" else None
    def query_selector_all(self, sel):
        return [self.slot] if self.revealed and sel == ".se-caption .se-text-paragraph" else []
    def evaluate(self, js): pass
    def click(self, force=False): self.revealed = True
    def inner_text(self): return self.slot.text


class _Keyboard:
    def __init__(self, page): self.page = page
    def type(self, text, delay=0): self.page.focused.text += text


class _Page:
    def __init__(self): self.focused, self.keyboard = None, _Keyboard(self)


class _Frame:
    def __init__(self, comps): self.comps = comps
    def query_selector_all(self, sel): return self.comps if sel == ".se-component.se-image" else []
    def evaluate(self, js): raise RuntimeError("no DOM in tests")


def test_fill_captions_in_order_and_refuses_mismatch(monkeypatch):
    from ai_workers import naver_paste_worker as w
    monkeypatch.setattr(w, "_human_delay", lambda *a, **k: None)
    page = _Page()
    comps = [_Component(page), _Component(page, hidden_until_click=True)]
    assert w._fill_photo_captions(page, _Frame(comps), ["첫 사진 설명", "둘째 사진 설명"]) == 2
    assert [c.slot.text for c in comps] == ["첫 사진 설명", "둘째 사진 설명"]

    page2 = _Page()
    one = [_Component(page2)]
    assert w._fill_photo_captions(page2, _Frame(one), ["가 설명입니다", "나 설명입니다"]) == 0
    assert one[0].slot.text == ""


def test_captions_copy_text_follows_post_order():
    from flet_app.views.naver_publish_view import captions_text
    campaign = {
        "content": "[IMAGE: b.jpg]\n글\n[IMAGE: a.jpg]",
        "storage_file_paths": ["a.jpg", "b.jpg"],
        "photo_captions": {"a.jpg": "에이", "b.jpg": "비"},
    }
    assert captions_text(campaign) == "1번 사진: 비\n2번 사진: 에이"
