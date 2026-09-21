"""Naver Blog semi-automatic publish.

Two-phase design, both requiring a *visible* (non-headless) browser on this
machine, since Naver's login flow (passkey/OTP) and the marketer's final
[발행] click both need a human watching the window:

1. `open_naver_login_session()` — a one-time (or occasional, once the session
   expires) admin action. Opens a real Chrome window, waits for login, and
   saves the session cookies to a local file. Blocking by design — the admin
   is sitting there watching it happen.
2. `trigger_naver_publish(campaign_id)` — spawns a detached subprocess
   (naver_paste_worker.py) so the Streamlit rerun returns immediately while a
   separate Chrome window opens, pastes title/body/photos into Smart Editor
   ONE using the saved session, and leaves the browser open for the admin to
   review and click [발행] themselves — the "semi" in semi-automatic.

Naver session cookies deliberately never enter the database, not even this
local one: they're the one credential here that grants access to a live
publishing account, so they stay in a single file that's easy to delete.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

from core.db import DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# core.db.DATA_DIR (not PROJECT_ROOT/"data") — this must be the exact same
# directory the rest of the app reads/writes its SQLite DB from. DATA_DIR
# falls back to a per-user OS folder when PROJECT_ROOT/data doesn't exist yet
# (see core/db.py's _default_data_dir). Hardcoding PROJECT_ROOT/"data" here
# used to create that directory as a side effect of the first Naver login
# (via NAVER_STATE_FILE.parent.mkdir below) — which then made every *new*
# process (e.g. the naver_paste_worker subprocess trigger_naver_publish
# spawns) re-resolve DATA_DIR to that now-existing-but-empty folder instead
# of the per-user one the already-running app was using, so the publish
# worker looked up campaigns in a different, empty database and always
# reported "campaign not found".
NAVER_STATE_FILE = DATA_DIR / "naver_state.json"


# Publishing runs as browser automation, so its failures arrive as Playwright
# and OS exceptions — "net::ERR_NAME_NOT_RESOLVED", multi-line "Call log:"
# dumps, "Target page, context or browser has been closed". Those are useless
# to the marketer using this page, but they're the only diagnostic if the
# automation breaks. `format_publish_error` therefore stores a plain sentence
# for the UI with the raw text kept after DETAIL_SEPARATOR, and the view shows
# only the sentence unless someone opens the details.
DETAIL_SEPARATOR = "\n\n[기술 상세]\n"

_ERROR_HINTS = (
    (("팝업", "이어서 작성"), "네이버가 '이어서 작성하시겠습니까?' 같은 팝업을 띄웠는데 자동으로 닫지 못했습니다. [다시 게시]로 한 번 더 시도해주세요. 계속 실패하면 네이버 블로그에 직접 로그인해 임시저장된 글이 있는지 확인해주세요."),
    (("nidlogin", "login", "로그인"), "네이버 로그인이 풀렸습니다. 위에서 다시 로그인한 뒤 게시해주세요."),
    (("timeout", "timed out"), "네이버 편집기가 제때 열리지 않았습니다. 잠시 후 다시 시도해주세요."),
    (("net::err", "econnrefused", "dns", "connection"), "네이버에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해주세요."),
    (("has been closed", "is closed", "closed"), "게시 도중 Chrome 창이 닫혔습니다. 창을 닫지 말고 다시 시도해주세요."),
    (("executable doesn't exist", "browsertype.launch", "chromium"), "Chrome을 실행하지 못했습니다. Chrome이 설치되어 있는지 확인해주세요."),
)


def format_publish_error(exc: Exception) -> str:
    """Plain-Korean sentence for the UI, raw exception kept for diagnosis."""
    raw = str(exc).strip()
    haystack = raw.lower()
    summary = "게시 중 문제가 발생해 중단되었습니다. 다시 시도해주세요."
    for needles, message in _ERROR_HINTS:
        if any(needle in haystack for needle in needles):
            summary = message
            break
    return f"{summary}{DETAIL_SEPARATOR}{raw}" if raw else summary


def split_publish_error(message: str):
    """Returns (사용자용 문장, 기술 상세 or '')."""
    if not message:
        return "", ""
    summary, _, detail = message.partition(DETAIL_SEPARATOR)
    return summary.strip(), detail.strip()


def naver_session_exists() -> bool:
    return NAVER_STATE_FILE.exists()


def clear_naver_session() -> None:
    NAVER_STATE_FILE.unlink(missing_ok=True)


def launch_browser(p, headless: bool = False):
    """Prefers the system-installed Chrome (more convincing to Naver's bot
    detection than bundled Chromium) with automation-tell flags stripped."""
    args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
    if not sys.platform.startswith("darwin"):
        args += ["--no-sandbox", "--disable-dev-shm-usage"]

    chrome_app_mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if sys.platform == "darwin" and Path(chrome_app_mac).exists():
        try:
            return p.chromium.launch(headless=headless, executable_path=chrome_app_mac, args=args)
        except Exception as exc:  # noqa: BLE001
            print(f"[naver_publisher] system Chrome launch failed, trying channel='chrome': {exc}")

    try:
        return p.chromium.launch(headless=headless, channel="chrome", args=args)
    except Exception as exc:  # noqa: BLE001
        print(f"[naver_publisher] channel='chrome' failed, falling back to bundled Chromium: {exc}")

    return p.chromium.launch(headless=headless, args=args)


def open_naver_login_session(blog_id: str = "") -> Dict[str, Any]:
    """Blocking — opens a visible Chrome window and waits up to 2 minutes for
    the admin to finish logging in, then saves the session."""
    from playwright.sync_api import sync_playwright

    NAVER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean_id = blog_id.strip()
    target_url = (
        f"https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fblog.naver.com%2F{clean_id}%3FRedirect%3DWrite"
        if clean_id
        else "https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fsection.blog.naver.com"
    )

    with sync_playwright() as p:
        browser = launch_browser(p, headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded")

        logged_in = False
        for _ in range(120):
            try:
                if page.is_closed():
                    break
                cookies = context.cookies()
                if any(c.get("name") in ("NID_AUT", "NID_SES") for c in cookies):
                    time.sleep(2.0)  # let Naver finish setting all auth cookies before snapshotting
                    context.storage_state(path=str(NAVER_STATE_FILE))
                    logged_in = True
                    break
            except Exception:
                break
            time.sleep(1)

        browser.close()

    if logged_in and NAVER_STATE_FILE.exists():
        return {"success": True, "message": "🔑 네이버 로그인 세션이 저장되었습니다. 이제 게시를 진행할 수 있습니다."}

    clear_naver_session()
    return {"success": False, "message": "⚠️ 로그인이 완료되지 않은 상태에서 창이 닫혔습니다. 다시 시도해주세요."}


def trigger_naver_publish(campaign_id: str) -> Dict[str, Any]:
    """Non-blocking — hands off to naver_paste_worker.py as a detached
    subprocess and returns immediately, so Streamlit isn't held for the
    minute-plus the browser automation takes."""
    if not naver_session_exists():
        return {"success": False, "message": "네이버 로그인 세션이 없습니다. 먼저 로그인 세션을 저장하세요."}

    subprocess.Popen(
        [sys.executable, "-m", "ai_workers.naver_paste_worker", campaign_id],
        cwd=str(PROJECT_ROOT),
    )
    return {
        "success": True,
        "message": "게시 작업을 시작했습니다. 잠시 후 Chrome 창이 열리면 내용을 확인하고 [발행] 버튼을 직접 눌러주세요.",
    }
