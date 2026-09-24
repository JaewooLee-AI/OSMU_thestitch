"""Flet desktop entry point for the OSMU workbench — the app's only UI.

core/ (data, storage, crypto), ai_workers/ (LLM pipeline, Naver automation)
and flet_app/ (views, simulators) are the whole application. Every view's
`build(page, state)` rebuilds its screen from `core.repo` each time it is
selected in the navigation rail.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows에서 콘솔(터미널)이 없거나(예: 더블클릭 실행, 빌드된 exe) 로케일이
# 영어가 아니면, sys.stdout/stderr의 기본 인코딩이 UTF-8이 아닌 legacy
# 코드페이지(한국어 환경은 cp949)로 잡힌다. 이 앱과 ai_workers/*의 print()들은
# 이모지 등 그 코드페이지에 없는 문자를 자주 찍는데, 인코딩 못 하면
# UnicodeEncodeError가 그 print() 호출부를 그대로 깨뜨린다 — 콘솔 로그 하나
# 때문에 실제 생성 파이프라인 전체가 조용히 실패하는 식으로 나타났다
# (ai_workers/content_writer.py의 _report()에서 실제로 재현됨). errors="replace"로
# 재설정해 두면 문자가 안 예쁘게 나올 수는 있어도 여기서 예외가 나는 일은
# 없다. reconfigure()가 없는 극히 오래된 스트림 대비 hasattr로 방어.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import flet as ft

# flet_app/ sits next to core/ and ai_workers/ at the project root, so make
# sure that root is importable regardless of the CWD `flet run`/`python` is
# invoked from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# .env must be loaded *before* core is imported: core.db resolves
# OSMU_DATA_DIR at import time, and core.crypto_utils reads
# ENCRYPTION_MASTER_KEY_BASE64. Without this, a master key moved into .env
# (as HANDOFF.md recommends) was invisible to this app, which then generated a
# fresh key and could no longer decrypt any stored API key.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv()  # CWD .env too, for `flet run` from elsewhere; never overrides the above

from core import repo  # noqa: E402
from core.brand_seed import apply_seed_fixes, seed_if_empty  # noqa: E402
from core.db import init_db  # noqa: E402

from flet_app.state import FONT_SCALE_STATE_KEY, AppState  # noqa: E402
from flet_app.theme import DEFAULT_FONT_SCALE, build_theme  # noqa: E402
from flet_app.views import (  # noqa: E402
    brand_kit_view,
    dashboard_view,
    naver_publish_view,
    news_curation_view,
    settings_view,
    workbench_view,
)

# (아이콘, 선택됐을 때 아이콘, 라벨, 뷰 모듈 — None이면 플레이스홀더)
DESTINATIONS = [
    (ft.Icons.DASHBOARD_OUTLINED, ft.Icons.DASHBOARD, "대시보드", dashboard_view),
    (ft.Icons.EDIT_OUTLINED, ft.Icons.EDIT, "워크벤치", workbench_view),
    (ft.Icons.ARTICLE_OUTLINED, ft.Icons.ARTICLE, "뉴스 큐레이션", news_curation_view),
    (ft.Icons.STYLE_OUTLINED, ft.Icons.STYLE, "브랜드 킷", brand_kit_view),
    (ft.Icons.SEND_OUTLINED, ft.Icons.SEND, "네이버 게시", naver_publish_view),
    (ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS, "설정 · 토큰", settings_view),
]
DEFAULT_INDEX = 3  # 🧵 브랜드 킷
WORKBENCH_INDEX = 1  # DESTINATIONS 안 "워크벤치" 위치 — state.navigate_to_workbench가 씀


def _placeholder(label: str) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(label, size=20, weight=ft.FontWeight.BOLD),
                ft.Text("다음 작업에서 포팅될 화면입니다.", color=ft.Colors.GREY),
            ]
        ),
        padding=24,
    )


def main(page: ft.Page) -> None:
    init_db()
    # Generation runs in this process, so anything still 'processing' at
    # startup was cut off when the app last closed — see the repo docstring.
    interrupted = repo.recover_interrupted_processing()
    if interrupted:
        print(f"[main] {interrupted}개 콘텐츠가 이전 실행에서 생성 도중 중단되어 '실패'로 표시했습니다.")
    seeded = seed_if_empty()
    apply_seed_fixes()  # see core/brand_seed.SEED_FIXES

    saved_scale = (repo.get_app_state(FONT_SCALE_STATE_KEY) or {}).get("value")
    state = AppState(font_scale=float(saved_scale) if saved_scale else DEFAULT_FONT_SCALE)

    page.title = "더스티치 OSMU 워크벤치"
    # 브랜드 팔레트가 밝은 배경 기준이라 시스템이 다크 모드여도 항상 라이트로
    # 고정한다 — 안 그러면 커스텀 텍스트 테마 색상이 다크 모드 기본값과 어긋나
    # 글자가 안 보이는 문제가 생긴다.
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = build_theme(state.font_scale)
    page.window.width = 1280
    page.window.height = 860

    body = ft.Container(expand=True, padding=20, content=ft.Text(""))
    current_index = [DEFAULT_INDEX]

    def show(index: int) -> None:
        current_index[0] = index
        _, _, label, module = DESTINATIONS[index]
        body.content = module.build(page, state) if module else _placeholder(label)
        body.update()

    def rerender() -> None:
        # 글자 크기가 바뀌면 기본 스타일(page.theme)과 지금 보고 있는 화면 둘 다
        # 다시 만들어야 한다 — 이미 만들어진 컨트롤들의 size=는 소급 적용되지 않는다.
        page.theme = build_theme(state.font_scale)
        show(current_index[0])
        page.update()

    state.request_rerender = rerender

    rail = ft.NavigationRail(
        selected_index=DEFAULT_INDEX,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=90,
        destinations=[
            ft.NavigationRailDestination(icon=icon, selected_icon=selected_icon, label=label)
            for icon, selected_icon, label, _ in DESTINATIONS
        ],
        on_change=lambda e: show(e.control.selected_index),
    )

    def go_to_workbench() -> None:
        rail.selected_index = WORKBENCH_INDEX
        rail.update()
        show(WORKBENCH_INDEX)

    state.navigate_to_workbench = go_to_workbench

    page.add(
        ft.Row(
            [rail, ft.VerticalDivider(width=1), body],
            expand=True,
        )
    )

    if seeded:
        page.show_dialog(ft.SnackBar(ft.Text("회사 자료 기반 Brand Kit을 초기 세팅했습니다.")))

    brand_kit = repo.get_brand_kit()
    page.window.title = f"{brand_kit.get('sub_brand') or ''} OSMU 워크벤치".strip()

    show(DEFAULT_INDEX)


if __name__ == "__main__":
    ft.run(main)
