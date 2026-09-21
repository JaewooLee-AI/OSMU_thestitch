"""Subprocess entrypoint spawned by ai_workers.naver_publisher.trigger_naver_publish.
Run as: python -m ai_workers.naver_paste_worker <campaign_id>

DOM automation against Naver's Smart Editor ONE, adapted from
the prior cjc_blog_v2 project's paste_worker.py's draft-popup and strikethrough cleanup quirks.
Image placement, however, is NOT ported from cjc: that project pastes the whole HTML
body at once with visible `[PHOTO_LOCATION_MARKER_N]` text markers and then
searches the rendered DOM for each marker to know where to insert a photo.
That approach was tried here twice (matching its marker text, TreeWalker
logic, and popup selectors byte-for-byte) and still failed against this
account's live editor — the marker text could not be reliably found after
paste. Instead, this worker inserts content *sequentially*, one segment at a
time, in document order: paste a text paragraph, then upload the next photo,
then paste the next paragraph, and so on. Each insertion lands wherever the
editor's own cursor already is (exactly like a human typing/uploading in
order), so there is no DOM search step to fail.

This is a *human-in-the-loop* finish line, not full automation: once the
title/body/images are pasted in, the browser is left open for the admin to
review and click Naver's own [발행] button themselves. Never run this
headless — Naver's anti-bot detection and the final publish click both
assume a real person is at the keyboard.
"""
from __future__ import annotations

import html
import sys
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory

# 이 워커는 detached subprocess로 별도 실행된다(naver_publisher.trigger_naver_publish가
# `sys.executable -m ai_workers.naver_paste_worker`로 띄움) — flet_app/main.py의
# stdout 재설정은 그 GUI 프로세스에만 적용되고 이 프로세스는 물려받지 않는다.
# Windows 콘솔의 기본 코드페이지(한국어 환경은 cp949)가 이모지·em dash 같은 문자를
# 못 찍으면 아래 곳곳의 print()가 UnicodeEncodeError로 죽는데, 그게 하필
# `repo.update_campaign(status="published")` 바로 다음 줄(예: "완료 — ...")에서
# 나면 방금 성공한 게시가 그 자리에서 실패로 덮어써지고 브라우저도 조기에 닫혀버린다
# — 실제로 재현됨. 콘솔 로그 한 줄 때문에 성공한 게시가 실패로 둔갑하는 일이 없도록
# 다른 곳에서 이미 쓴 것과 같은 방식으로 프로세스 시작 시점에 한 번 막아둔다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from ai_workers.naver_publisher import NAVER_STATE_FILE, format_publish_error, launch_browser
from ai_workers.photo_placement import split_segments
from core import repo, storage
from core.clipboard_utils import copy_html_to_clipboard

_HUMAN_DELAY = (0.15, 0.35)


def _human_delay(lo: float = _HUMAN_DELAY[0], hi: float = _HUMAN_DELAY[1]) -> None:
    import random

    time.sleep(random.uniform(lo, hi))


def _text_segment_to_html(text: str) -> str:
    """Wraps each line in a <p> for the rich-HTML clipboard paste.

    Escapes first: the body is Korean marketing prose, but an unescaped "&" or
    "<" (a stray "<브랜드>" or "A&B") would be parsed as markup by Naver's
    editor and silently swallow the surrounding text.
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def _stage_images(rel_paths: list, dest_dir: Path) -> list:
    """Copies attached photos into a temp dir so Playwright's file chooser has
    real paths to hand Naver. (In OSMU_admin this downloaded each file from
    Supabase Storage; with local storage it's a straight read, and the copy
    exists only because the upload dialog wants a stable path.)"""
    local_paths = []
    for i, rel_path in enumerate(rel_paths):
        try:
            data = storage.read_bytes(rel_path)
            local_path = dest_dir / f"photo_{i}{Path(rel_path).suffix or '.jpg'}"
            local_path.write_bytes(data)
            local_paths.append(local_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[naver_paste_worker] failed to stage {rel_path}: {exc}")
            local_paths.append(None)  # keep index alignment with markers
    return local_paths


def _find_editor_frame(page):
    try:
        frame = page.frame(name="mainFrame")
        if not frame:
            page.wait_for_selector("#mainFrame", timeout=4000)
            frame = page.frame(name="mainFrame")
        return frame or page
    except Exception:
        return page


_CANCEL_SELECTORS = [
    ".se-popup-button-cancel",
    "button.se-popup-button-cancel",
    ".se-popup-button.se-popup-button-cancel",
    ".se-help-panel-close-button",
    ".se-popup-container button:nth-child(1)",
    # 페이지 전체에서 "취소"/"아니오" 텍스트를 찾던 이전 버전은 팝업과 무관한
    # 버튼까지 잡는 문제가 있었다(바로 아래 설명). 그렇다고 완전히 빼버리면
    # .se-popup-button-cancel 계열 클래스가 이번 팝업의 실제 버튼과 안
    # 맞을 때 아예 못 닫는 문제가 생긴다 — 그래서 .se-popup-container /
    # .se-popup-dim 안으로 범위를 좁혀서 텍스트 매칭을 다시 넣는다: 팝업
    # 컨테이너 밖의 동명 버튼은 여전히 안 걸리면서, 팝업 자체의 버튼
    # 클래스가 예상과 달라도 문구로 찾아낼 수 있다.
    ".se-popup-container button:has-text('취소')",
    ".se-popup-container button:has-text('아니오')",
    ".se-popup-dim button:has-text('취소')",
    # 2026-09 라이브 계정에서 브라우저 콘솔로 직접 확인: "이어서
    # 작성하시겠습니까?" 팝업이 se-popup-* 클래스가 아니라
    # layer_popup__<해시> 형태의 CSS 모듈 클래스로 렌더링되고 있었다(예:
    # layer_popup__MFPwH). 위 se-popup-* 셀렉터들은 이 팝업을 전혀 못
    # 찾아서, _popup_still_visible()이 "팝업 없음"으로 오판 → 아래
    # _dismiss_draft_restore_popup()의 재시도 루프(Escape 포함)가 아예
    # 한 번도 실행되지 않고 바로 제목 입력으로 넘어가 버렸다 — 그 결과
    # 사람이 수동으로 취소를 눌러야 했고, 그 사이 제목 입력이 날아갔다.
    # 해시 부분은 배포마다 바뀔 수 있어 접두사(layer_popup__)만으로 찾는다.
    "[class*='layer_popup__'] button:has-text('취소')",
    "[class*='layer_popup__'] button:has-text('아니오')",
    "[class*='layer_popup__'] button:has-text('닫기')",
    "[class*='layer_popup__'] button:has-text('괜찮')",
    "[class*='layer_popup__'] button:nth-child(1)",
]

# _CANCEL_SELECTORS의 텍스트/위치 기반 셀렉터가 이번에도 안 맞을 경우를
# 대비해, 팝업 컨테이너 자체는 이 목록으로 따로 감지한다 — 못 찾은 버튼을
# 추측해서 누르는 대신, 컨테이너 안의 실제 버튼 HTML을 로그로 남겨서
# 다음 실패 시 정확한 클래스명을 바로 알 수 있게 한다(더 이상 브라우저
# 콘솔을 직접 열어 확인할 필요 없이).
_POPUP_CONTAINER_SELECTORS = [
    ".se-popup-container",
    ".se-popup-dim",
    "[class*='layer_popup__']",
]

# 예전엔 텍스트 매칭이 .se-popup-container 범위 없이 페이지 전체
# (button:has-text('취소'))였다 — 실제 라이브 네이버 글쓰기 페이지에서
# 테스트해보니(더스티치), 이어서 작성하시겠습니까 팝업은
# .se-popup-button-cancel로 attempt 1에서 바로 닫혔는데, 그 뒤로도 범위
# 없는 button:has-text('취소')가 페이지 어딘가의 *다른*(팝업과 무관한)
# "취소" 버튼을 계속 찾아내는 바람에 20번을 다 돌 때까지 "아직 팝업이
# 있다"고 오판했다 — 결국 타임아웃되거나 사용자가 아무 반응이 없다고
# 여겨 Chrome 창을 닫아버려 TargetClosedError로 게시가
# 죽었다(로그로 확인됨). 그렇다고 텍스트 매칭을 완전히 빼버렸더니 이번엔
# 반대로 "팝업이 전혀 안 닫힌다"는 문제가 났다 — 클래스만으로는 이번
# 팝업의 실제 버튼을 못 찾은 것으로 보인다. 그래서 위처럼
# .se-popup-container/.se-popup-dim으로 범위를 좁힌 텍스트 매칭을
# 다시 넣었다: 페이지 전체가 아니라 팝업 컨테이너 안에서만 찾는다.


def _popup_still_visible(page, editor_frame) -> bool:
    """True if a known cancel button OR the popup container itself is visible.

    Started out checking only _CANCEL_SELECTORS (deliberately narrow,
    Naver-specific button selectors — a broader check like generic
    dialog-role containers or free-text "취소" matches page-wide used to
    match ordinary Smart Editor chrome and make every publish fail/loop with
    a false "there's a popup" reading). That was too narrow the other way:
    when the popup's actual button text doesn't match any of our guesses
    (confirmed live — a layer_popup__<hash> container was visible but none
    of its buttons said 취소/아니오/닫기/괜찮), this returned False even
    though a popup really was blocking the page, so the retry loop below
    (Escape presses included) never even ran — the popup was just left
    sitting there. Now also treats the container itself
    (_POPUP_CONTAINER_SELECTORS) as "a popup is showing", independent of
    whether we can identify its button yet.
    """
    for context in (page, editor_frame):
        for sel in _CANCEL_SELECTORS + _POPUP_CONTAINER_SELECTORS:
            try:
                el = context.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                pass
    return False


def _log_popup_html(page, editor_frame, when: str) -> None:
    """Dump the popup container's outerHTML so a real selector can be added
    later without needing another round of manual browser-console digging."""
    for context in (page, editor_frame):
        for sel in _POPUP_CONTAINER_SELECTORS:
            try:
                html = context.eval_on_selector(
                    sel, "(el) => el.outerHTML && el.outerHTML.slice(0, 1500)"
                )
                if html:
                    print(f"[naver_paste_worker] popup HTML ({when}, {sel}): {html}")
            except Exception:
                pass


def _dismiss_draft_restore_popup(page, editor_frame, timeout_s: float = 120.0) -> None:
    """Wait for the '이어서 작성하시겠습니까?' resume-draft popup to be gone,
    instead of guessing which button closes it.

    This used to try to click the right button itself — Escape, known
    selectors, then a generic "click whatever button is in the popup
    container" fallback. Live testing kept finding new cases: the popup's
    real button classes turned out to differ between accounts/deploys
    (.se-popup-button-cancel sometimes, a hashed layer_popup__<hash> other
    times), and an automated click racing a human's own manual click on the
    same popup repeatedly left the editor in a half-focused state (title or
    the first paragraph of body missing). None of that is fixable by adding
    more selectors — the popup a human sees is ground truth, this script
    isn't. So this now just waits: if nothing is showing, proceed
    immediately (unchanged fast path); if something is showing, do nothing
    to it and poll until the person closes it themselves (however they
    close it — cancel or confirm, script doesn't care), same principle as
    never clicking Naver's real [발행] button on the human's behalf.
    """
    if not _popup_still_visible(page, editor_frame):
        return

    _log_popup_html(page, editor_frame, "waiting-for-human")
    print(
        "[naver_paste_worker] 네이버가 팝업을 띄웠습니다 — 자동으로 누르지 않습니다. "
        "Chrome 창에서 직접 닫아주세요(취소든 확인이든 상관없습니다). "
        "닫으시면 이어서 진행합니다."
    )

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _popup_still_visible(page, editor_frame):
            print("[naver_paste_worker] popup cleared by hand — continuing")
            return
        time.sleep(0.5)

    _log_popup_html(page, editor_frame, "still-open-after-timeout")
    raise RuntimeError(
        f"네이버가 띄운 팝업이 {int(timeout_s)}초가 지나도 닫히지 않았습니다. "
        "Chrome 창에서 팝업을 직접 닫아주신 뒤 [다시 게시]로 다시 시도해주세요."
    )


def _query_selector_retry(editor_frame, selectors: list[str], timeout_s: float = 8.0):
    """Poll for a visible match instead of a single query_selector call.

    Right after _dismiss_draft_restore_popup() clicks 취소, Naver's editor is
    still re-initializing for a moment (confirmed live: a run right after a
    popup dismissal logged "no title element matched any selector" / "no
    body element found" even though the selectors are correct and normally
    match) — a single immediate query_selector can catch it mid-redraw and
    find nothing. Retrying for a few seconds rides out that transition
    instead of giving up on the first empty result.

    8s (not the original 3s): live testing showed the *slower* the person is
    to dismiss the popup, the longer this re-initialization drags on — a
    popup left open a while before being cancelled seems to make Naver do a
    heavier reload/cleanup than an immediate cancel does. 3s wasn't enough
    margin for that case.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for sel in selectors:
            try:
                el = editor_frame.query_selector(sel)
                if el and el.is_visible():
                    return el
            except Exception:
                pass
        time.sleep(0.3)
    return None



def _click_until_focused(editor_frame, el, when: str, attempts: int = 15) -> bool:
    """click(force=True) "succeeds" (no exception) even when the real
    editing surface — a nested iframe inside editor_frame — hasn't finished
    initializing yet, in which case focus silently stays on document.body
    and the keystrokes/paste that follow go nowhere. Confirmed live: the
    same click, at the same selector, reached the nested iframe on a later
    attempt in the same run but not an earlier one — a readiness/timing gap,
    not a wrong selector. So retry the click for a couple of seconds,
    checking that focus actually reached the iframe, instead of clicking
    once and assuming it landed."""
    for attempt in range(attempts):
        try:
            el.click(force=True)
        except Exception:
            pass
        time.sleep(0.3)
        try:
            tag = editor_frame.evaluate("() => document.activeElement && document.activeElement.tagName")
        except Exception:
            tag = None
        if tag == "IFRAME":
            print(f"[naver_paste_worker] click reached the editor ({when}, attempt {attempt + 1})")
            return True
    print(f"[naver_paste_worker] click never reached the nested editor after {attempts} attempts ({when}) — proceeding anyway")
    return False


def _strip_strikethrough(editor_frame) -> None:
    """Naver's editor sometimes auto-applies strikethrough on paste — force it off."""
    try:
        editor_frame.evaluate(
            """() => {
                const editor = document.querySelector('.se-main-container, .se-content, body');
                if (!editor) return;
                editor.querySelectorAll('s, del, strike, [style*="line-through"]').forEach((el) => {
                    el.style.textDecoration = 'none';
                    const parent = el.parentNode;
                    if (parent) {
                        while (el.firstChild) parent.insertBefore(el.firstChild, el);
                        parent.removeChild(el);
                    }
                });
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[naver_paste_worker] strikethrough cleanup note: {exc}")


def _upload_photo_at_cursor(page, editor_frame, file_path: Path) -> bool:
    # button.se-image-toolbar-button는 실제 계정의 에디터 DOM에서 확인된
    # 진짜 클래스명이다(브라우저 콘솔로 라이브 검증함). 그런데도 계속
    # 업로드가 실패했던 건 클래스가 틀려서가 아니라 아래의 is_visible()
    # 체크 때문이었을 가능성이 높다 — Playwright의 is_visible()은 툴바가
    # 스크롤 밖에 있거나 전환 애니메이션 중이면 실제로 클릭 가능한
    # 버튼도 False로 판정할 수 있다. 그래서 is_visible() 대신 스크롤 후
    # bounding box 크기로 "화면에 실제로 그려졌는가"만 확인하고, 어떤
    # 선택자가 왜 실패했는지 로그로 남겨 다음 실패 시 바로 원인을 알 수
    # 있게 한다.
    photo_selectors = [
        "button.se-image-toolbar-button",
        ".se-insert-menu-button-image",
        "button.se-document-toolbar-image-button",
        "button[data-name='image']",
        ".se-toolbar-button-image",
    ]
    for sel in photo_selectors:
        try:
            btn = editor_frame.query_selector(sel)
            if not btn:
                continue
            try:
                btn.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            box = btn.bounding_box()
            if not box or box["width"] == 0 or box["height"] == 0:
                print(f"[naver_paste_worker] photo selector '{sel}' matched but has no visible box — skipping")
                continue
            # force=True로 클릭해도 파일 선택창이 안 뜨는 게 라이브로 확인됐다
            # (버튼은 진짜로 보이고 클래스도 맞는데 filechooser 이벤트가 안
            # 옴 — 클릭 전후 툴바 목록도 동일해서 드롭다운이 뜨는 구조도
            # 아니었다). force=True는 Playwright의 정상 actionability 검사를
            # 건너뛰는데, 네이티브 파일 선택창을 여는 데 필요한 "신뢰할 수
            # 있는 사용자 클릭"으로 브라우저가 인정하지 않는 것으로 보인다.
            # 그래서 먼저 일반 클릭(정상 actionability 검사 포함)을 시도하고,
            # 그게 실패할 때만 force=True로 넘어간다.
            opened = False
            for use_force in (False, True):
                try:
                    with page.expect_file_chooser(timeout=3000) as fc_info:
                        btn.click(force=use_force, timeout=2000)
                    fc_info.value.set_files([str(file_path)])
                    _human_delay(1.5, 2.5)
                    opened = True
                    break
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[naver_paste_worker] photo selector '{sel}' "
                        f"({'force' if use_force else 'normal'} click) did not open a file chooser: {exc}"
                    )
            if opened:
                return True
            _log_visible_buttons(editor_frame, page, f"after clicking '{sel}'")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[naver_paste_worker] photo selector '{sel}' failed: {exc}")
            continue

    try:
        file_inputs = editor_frame.query_selector_all('input[type="file"]')
        if file_inputs:
            file_inputs[0].set_input_files([str(file_path)])
            _human_delay(1.5, 2.5)
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[naver_paste_worker] direct file input upload failed: {exc}")
    return False


def _log_visible_buttons(editor_frame, page, when: str) -> None:
    """List currently visible buttons/menu items so a follow-up submenu
    selector can be added without another round of manual console digging."""
    for context in (editor_frame, page):
        try:
            items = context.query_selector_all("button, [role='menuitem'], li")
            seen = []
            for el in items:
                try:
                    if el.is_visible():
                        text = (el.inner_text() or "").strip()[:20]
                        cls = (el.get_attribute("class") or "")[:60]
                        seen.append(f"'{text}'|{cls}")
                except Exception:
                    pass
            if seen:
                print(f"[naver_paste_worker] visible buttons ({when}): {seen[:25]}")
        except Exception:
            pass


def run(campaign_id: str) -> None:
    from playwright.sync_api import sync_playwright

    campaign = repo.get_campaign(campaign_id)
    if not campaign:
        print(f"[naver_paste_worker] campaign {campaign_id} not found")
        return

    brand_kit = repo.get_brand_kit()
    blog_id = (brand_kit.get("naver_blog_id") or "").strip()
    if not blog_id:
        repo.update_campaign(campaign_id, publish_error="네이버 블로그 아이디가 없습니다. [브랜드 킷] 페이지에서 먼저 등록해주세요.")
        return

    title = (campaign.get("title") or "").strip() or "(제목 없음)"
    content = campaign.get("content") or ""
    segments = split_segments(content)
    image_tags = [value for kind, value in segments if kind == "image"]

    with TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        local_images = _stage_images(image_tags, tmp_dir) if image_tags else []
        local_by_tag = dict(zip(image_tags, local_images))

        state_option = {"storage_state": str(NAVER_STATE_FILE)} if NAVER_STATE_FILE.exists() else {}
        target_url = f"https://blog.naver.com/{blog_id}?Redirect=Write"

        browser = None
        try:
            with sync_playwright() as p:
                browser = launch_browser(p, headless=False)
                context = browser.new_context(viewport={"width": 1280, "height": 900}, **state_option)
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.accept())

                page.goto(target_url, wait_until="domcontentloaded")
                # Windows는 백그라운드 프로세스가 새로 띄운 창을 자동으로
                # 앞으로 가져오지 않아서(포그라운드 탈취 방지), 이 창이
                # 관리자 눈에 안 띄는 다른 창 뒤에 뜬 채로 계속 대기하는
                # 일이 실제로 있었다 — 특히 팝업이 뜬 걸 사람이 못 보면
                # 위의 _dismiss_draft_restore_popup()이 계속 기다리기만 한다.
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                _human_delay(1.0, 1.5)

                if "nidlogin" in page.url:
                    repo.update_campaign(
                        campaign_id,
                        publish_error="네이버 로그인이 풀렸습니다. [네이버 게시] 페이지에서 다시 로그인한 뒤 게시해주세요.",
                    )
                    browser.close()
                    return

                editor_frame = _find_editor_frame(page)
                _dismiss_draft_restore_popup(page, editor_frame)

                modifier = "Meta" if sys.platform == "darwin" else "Control"

                # --- Body: clear first, before touching the title ---
                # This used to run *after* typing the title. Live testing
                # (thestitch) showed the title ending up missing and the
                # first paragraph of body text landing partially or not at
                # all, with an image showing up first instead — consistent
                # with this Ctrl+A/Delete not staying scoped to just the body
                # (Smart Editor ONE's title and body are components inside
                # the same document, and a "select all" issued a beat after
                # the title was typed can catch more than intended) and
                # wiping content that was already typed in. Clearing the body
                # *before* anything meaningful has been entered anywhere
                # means there is nothing upstream left for an over-broad
                # select-all to destroy.
                body_el = _query_selector_retry(editor_frame, [".se-main-container", ".se-component-text", ".se-content"])
                if body_el:
                    _click_until_focused(editor_frame, body_el, "body (clear step)")
                    _human_delay(0.3, 0.5)
                    page.keyboard.press(f"{modifier}+a")
                    page.keyboard.press("Delete")
                    _human_delay(0.5, 0.8)

                # --- Title ---
                title_selectors = [
                    ".se-component-title .se-text-paragraph",
                    ".se-document-title .se-text-paragraph",
                    ".se-title-text",
                    "div.se-component-title p",
                ]
                title_el = _query_selector_retry(editor_frame, title_selectors)
                if title_el:
                    _click_until_focused(editor_frame, title_el, "title")
                    _human_delay(0.2, 0.3)
                    page.keyboard.press(f"{modifier}+a")
                    page.keyboard.press("Backspace")
                    page.keyboard.type(title, delay=15)
                    _human_delay(0.4, 0.6)
                else:
                    print("[naver_paste_worker] no title element matched any selector — title was never clicked")

                # 본문은 이미 위에서 비워뒀지만, 그 클릭 이후로 제목을 입력하며
                # 포커스가 제목 쪽으로 옮겨갔다 — 여기서 다시 본문을 클릭해
                # 포커스를 돌려놓지 않으면 아래 붙여넣기가 전부 제목 칸에
                # 이어서 들어간다(실제로 재현됨). 이미 비어 있으니 다시
                # 전체선택+삭제는 반복하지 않는다. 제목을 입력하는 동안
                # 에디터가 본문 요소를 다시 그렸을 수 있어(위에서 찾아둔
                # body_el이 이제 DOM에서 떨어져 나갔을 수 있어), 클릭 직전에
                # 한 번 더 찾는다 — 못 찾으면 예전 핸들이라도 시도해본다.
                body_el_refocus = _query_selector_retry(
                    editor_frame, [".se-main-container", ".se-component-text", ".se-content"], timeout_s=4.0
                ) or body_el
                if body_el_refocus:
                    _click_until_focused(editor_frame, body_el_refocus, "body refocus (before paste loop)")
                    _human_delay(0.2, 0.3)
                else:
                    print("[naver_paste_worker] no body element found to refocus before paste loop")

                for kind, value in segments:
                    if kind == "text":
                        segment_html = _text_segment_to_html(value)
                        if not segment_html:
                            continue
                        copy_html_to_clipboard(segment_html)
                        page.keyboard.press(f"{modifier}+v")
                        # Naver's editor is still re-parsing the pasted HTML into
                        # its own component structure for a moment after paste —
                        # give it time to settle before the next action (paste or
                        # upload) touches the same area, instead of racing it.
                        _human_delay(1.5, 2.0)
                        _strip_strikethrough(editor_frame)
                    else:
                        local_path = local_by_tag.get(value)
                        if local_path is None:
                            print(f"[naver_paste_worker] no local file for image '{value}' — skipping")
                            continue
                        uploaded = _upload_photo_at_cursor(page, editor_frame, local_path)
                        if not uploaded:
                            print(f"[naver_paste_worker] photo upload failed for '{value}'")
                        _human_delay(1.5, 2.0)

                # 제목이 비어 보이면 다시 채운다 — 위에서 본문보다 먼저 채우도록
                # 순서를 바꿨지만, 그것과 무관하게 다른 경로로 지워질 가능성까지
                # 막는 마지막 안전장치. 원래 title이 비어 있지 않을 때만 채운다.
                #
                # title_el을 그대로 재사용하지 않고 다시 찾는다 — 위에서 잡아둔
                # title_el 핸들은 그 이후 여러 번의 재렌더링(본문 붙여넣기,
                # 사진 삽입)을 거치며 DOM에서 이미 떨어져 나갔을(stale) 수 있고,
                # 그 상태로 inner_text()를 읽으면 예외가 난다. 예전 코드는 그
                # 예외를 "못 읽으면 이미 맞다고 가정"하고 넘어갔는데, 이건
                # 읽기 실패를 성공으로 착각하는 것이었다 — 실제로 라이브에서
                # 제목이 비어 있었는데도 이 안전장치가 전혀 작동하지 않은 채로
                # "paste complete"까지 가버린 사례가 있었다.
                if title.strip():
                    fresh_title_el = _query_selector_retry(editor_frame, title_selectors, timeout_s=4.0) or title_el
                    current_title = ""
                    if fresh_title_el:
                        try:
                            current_title = (fresh_title_el.inner_text() or "").strip()
                        except Exception as exc:  # noqa: BLE001
                            print(f"[naver_paste_worker] could not read title text, assuming it needs retyping: {exc}")
                    if fresh_title_el and not current_title:
                        print("[naver_paste_worker] title ended up empty after body paste — retyping it")
                        fresh_title_el.click(force=True)
                        _human_delay(0.3, 0.5)
                        page.keyboard.press(f"{modifier}+a")
                        page.keyboard.press("Backspace")
                        page.keyboard.type(title, delay=15)
                        _human_delay(0.4, 0.6)

                # 여기서 status="published"로 바꾸지 않는다 — 이 자동화는 제목·
                # 본문·사진을 붙여넣기만 할 뿐, 네이버의 진짜 [발행] 버튼은
                # 절대 대신 눌러주지 않는다(사람이 직접 확인하고 눌러야 하는
                # human-in-the-loop 설계). 예전엔 여기서 바로 published로
                # 표시했는데, 그러면 사람이 실제로 발행을 누르기도 전에(또는
                # 창이 대기 시간 만료나 실수로 닫혀 끝내 못 누른 경우에도)
                # 앱에는 "게시 완료"로 영구히 잘못 남는다 — 실제로 재현됨(네이버
                # 블로그에는 그 글이 없는데 앱은 완료로 표시). 붙여넣기만 끝난
                # 상태로는 ready_to_publish를 그대로 둔다 — 사람이 네이버 창에서
                # 직접 [발행]을 확인한 뒤, [네이버 게시] 화면의 [✅ 수동으로 완료]
                # 를 눌러야 비로소 published가 된다(그 버튼도 이미 있고 하는 일도
                # 동일하다 — 그게 진짜 발행 확인 시점이라는 차이뿐).
                print("[naver_paste_worker] paste complete — waiting for admin to review, click 발행 in Naver, "
                      "then click [✅ 수동으로 완료] in the app")

                # Keep the browser open so the admin can review/publish by hand.
                for _ in range(300):
                    try:
                        if page.is_closed() or len(context.pages) == 0:
                            break
                    except Exception:
                        break
                    time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            repo.update_campaign(campaign_id, publish_error=format_publish_error(exc))
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m ai_workers.naver_paste_worker <campaign_id>")
        sys.exit(1)
    run(sys.argv[1])
