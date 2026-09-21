"""Flet-native replacement for core/theme.py's CSS injection.

The Streamlit version injects assets/custom.css and returns HTML-string
helpers (`stat_card`, `status_chip`, `palette_html`) fed to
`st.markdown(unsafe_allow_html=True)`. Flet desktop has no CSS/HTML layer for
native controls, so the same visual language (brand colors, stat cards,
status chips, the color palette swatches) is rebuilt here as small
Container/Text control trees instead of HTML strings.

Also owns font-size scaling for the ⚙️ 설정 화면의 글자 크기 조정 기능. Flet/
Flutter doesn't expose a single "scale every Text control, including ones
with an explicit size=" knob, so scaling has two halves: `build_theme(scale)`
sets the *default* sizes controls fall back to when they don't set their own
size (TextField labels/input text, etc.), and `fs()` is applied by callers at
every explicit `size=NN` literal for everything else (captions, headers,
stat cards). Both must be driven by the same `scale` value.
"""
from __future__ import annotations

import flet as ft

from core.brand_seed import BRAND_COLORS

STATUS_LABELS = {
    "awaiting_media": "소재 대기",
    "queued": "대기열",
    "processing": "생성 중",
    "draft": "초안 완료",
    "ready_to_publish": "게시 대기",
    "published": "게시 완료",
    "failed": "실패",
}

# 설정 화면의 글자 크기 슬라이더가 고르는 값 — Slider(min=0.8, max=1.5)와 짝을 이룸.
DEFAULT_FONT_SCALE = 1.0
MIN_FONT_SCALE = 0.8
MAX_FONT_SCALE = 1.5


def fs(base: int, scale: float) -> int:
    """base 크기(디자인 기준 14pt 시절 값)에 사용자가 고른 배율을 적용한다."""
    return round(base * scale)


def build_theme(scale: float = DEFAULT_FONT_SCALE) -> ft.Theme:
    return ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=BRAND_COLORS["primary"],
            secondary=BRAND_COLORS["secondary"],
            tertiary=BRAND_COLORS["accent"],
            surface=BRAND_COLORS["bg"],
            on_surface=BRAND_COLORS["text"],
        ),
        # 명시적 size=를 지정하지 않은 컨트롤(TextField 라벨·입력 글자 등)이
        # 기본값으로 삼는 크기. Material 기본값(body_medium=14 등)에 배율을
        # 곱해서 앱 전역 기본 글자 크기가 함께 커지고 작아지게 만든다.
        #
        # color를 반드시 명시한다 — 비워두면 Flutter의 기본 다크/라이트 테마
        # 색상 계산이 이 커스텀 TextTheme과 어긋나, 시스템이 다크 모드일 때
        # (이 맥이 다크 모드였음) 밝은 배경에 밝은 글자가 나와 텍스트가
        # 전부 안 보이는 문제가 실제로 발생했다.
        text_theme=ft.TextTheme(
            body_small=ft.TextStyle(size=fs(12, scale), color=BRAND_COLORS["text"]),
            body_medium=ft.TextStyle(size=fs(14, scale), color=BRAND_COLORS["text"]),
            body_large=ft.TextStyle(size=fs(16, scale), color=BRAND_COLORS["text"]),
            label_medium=ft.TextStyle(size=fs(12, scale), color=BRAND_COLORS["text"]),
            label_large=ft.TextStyle(size=fs(14, scale), color=BRAND_COLORS["text"]),
            title_medium=ft.TextStyle(size=fs(16, scale), color=BRAND_COLORS["text"]),
            title_large=ft.TextStyle(size=fs(22, scale), color=BRAND_COLORS["text"]),
            headline_small=ft.TextStyle(size=fs(24, scale), color=BRAND_COLORS["text"]),
        ),
    )


def stat_card(label: str, value: str, note: str = "", scale: float = DEFAULT_FONT_SCALE) -> ft.Container:
    controls = [
        ft.Text(label, size=fs(12, scale), color=BRAND_COLORS["text_muted"]),
        ft.Text(value, size=fs(22, scale), weight=ft.FontWeight.BOLD, color=BRAND_COLORS["primary"]),
    ]
    if note:
        controls.append(ft.Text(note, size=fs(11, scale), color=BRAND_COLORS["text_muted"]))
    return ft.Container(
        content=ft.Column(controls, spacing=2, tight=True),
        bgcolor=ft.Colors.WHITE,
        border_radius=10,
        padding=14,
        border=ft.Border.all(1, "#14000000"),
    )


def status_chip(status: str, scale: float = DEFAULT_FONT_SCALE) -> ft.Container:
    return ft.Container(
        content=ft.Text(STATUS_LABELS.get(status, status), size=fs(11, scale), color=ft.Colors.WHITE),
        bgcolor=BRAND_COLORS["secondary"],
        border_radius=20,
        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
    )


def palette_row(scale: float = DEFAULT_FONT_SCALE) -> ft.Control:
    # 이 라벨은 core/theme.py의 palette_html()과 다릅니다 — 그쪽은 이전
    # 테넌트(더스티치/한복)의 색상명(자주·쪽빛·금박 등)이 리브랜딩 때 갱신되지
    # 않은 채 남아 있습니다. README.md가 설명하는 실제 CJC 팔레트 이름을 씁니다.
    names = {
        "primary": "네이비",
        "secondary": "틸",
        "accent": "골드",
        "mint": "민트",
        "bg": "웜페이퍼",
        "text": "먹",
    }
    swatches = [
        ft.Column(
            [
                ft.Container(width=48, height=48, bgcolor=BRAND_COLORS[key], border_radius=8),
                ft.Text(label, size=fs(11, scale)),
                ft.Text(BRAND_COLORS[key], size=fs(10, scale), color=BRAND_COLORS["text_muted"]),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
        )
        for key, label in names.items()
    ]
    return ft.Row(swatches, spacing=16)
