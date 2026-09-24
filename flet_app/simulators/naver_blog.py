"""네이버 블로그 시뮬레이터 — Flet 네이티브.

원본(`simulators/naver_blog.py`)이 잡는 문제 그대로: PC 880px ↔ 모바일 390px 폭
차이, 그리고 에디터 텍스트박스보다 실제 발행 화면에서 문단이 훨씬 길어 보이는
타이포(16px/1.8줄간격). `[IMAGE:]` 태그는 문서 순서 그대로 사진을 끼워 넣고,
태그되지 않은 첨부 사진은 경고 배너와 함께 끝에 갤러리로 붙인다 — 원본과 동일.
"""
from __future__ import annotations

import flet as ft

from core import storage
from flet_app.simulators.base import content_blocks

from flet_app.theme import BRAND_COLORS

PC_WIDTH = 880
MOBILE_WIDTH = 390


def _avatar() -> ft.Container:
    return ft.Container(
        width=44, height=44, border_radius=22,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
            colors=[BRAND_COLORS["primary"], BRAND_COLORS["accent"]],
        ),
    )


def _figure(rel_path: str) -> ft.Control:
    return ft.Column(
        [
            ft.Image(src=str(storage.abs_path(rel_path)), height=360, fit=ft.BoxFit.CONTAIN),
            ft.Text("사진 설명을 입력하세요.", size=13, color="#AAAAAA", text_align=ft.TextAlign.CENTER),
        ],
        spacing=6,
    )


def render(campaign: dict, is_mobile: bool = False, blog_name: str = "공식 블로그") -> ft.Control:
    title = campaign.get("title") or "(제목 없음)"
    content = campaign.get("content") or ""
    attached = campaign.get("storage_file_paths") or []
    hashtags = campaign.get("naver_hashtags") or []

    blocks = content_blocks(content)
    tagged = {value for kind, value in blocks if kind == "image"}
    untagged = [p for p in attached if p not in tagged]

    body_controls: list[ft.Control] = []
    for kind, value in blocks:
        if kind == "text":
            body_controls.append(ft.Text(value, size=15 if is_mobile else 16, color="#333333"))
        else:
            body_controls.append(_figure(value))

    if not body_controls:
        body_controls.append(ft.Text("본문이 비어 있습니다.", color="#999999"))

    if untagged:
        body_controls.append(ft.Container(
            content=ft.Text(
                f"본문에 배치되지 않은 사진 {len(untagged)}장이 글 끝에 붙습니다. "
                "위치를 지정하려면 본문에서 [IMAGE: 경로]를 옮기세요.",
                size=12, color="#C05621",
            ),
            bgcolor="#FFFAF0", border=ft.Border.all(1, "#FBD38D"), border_radius=6, padding=10,
        ))
        for path in untagged:
            body_controls.append(_figure(path))

    controls: list[ft.Control] = [
        ft.Text(title, size=24 if is_mobile else 32, weight=ft.FontWeight.BOLD, color="#222222"),
        ft.Divider(color="#F1F5F9"),
        ft.Row([
            _avatar(),
            ft.Column([
                ft.Text(blog_name, size=15, weight=ft.FontWeight.BOLD, color="#222222"),
                ft.Text("방금 전 · 이웃추가", size=13, color="#888888"),
            ], spacing=2),
        ]),
        ft.Column(body_controls, spacing=20),
    ]
    if hashtags:
        controls.append(ft.Container(
            content=ft.Row(
                [ft.Container(
                    content=ft.Text(t, size=13, color="#2B4C8C"),
                    bgcolor="#F3F6FB", border_radius=999,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                ) for t in hashtags],
                wrap=True, spacing=6,
            ),
            padding=ft.Padding.only(top=20),
            border=ft.Border(top=ft.BorderSide(1, "#F1F5F9")),
        ))

    width = MOBILE_WIDTH if is_mobile else PC_WIDTH
    return ft.Container(
        width=width,
        height=780 if is_mobile else 1000,
        bgcolor=ft.Colors.WHITE,
        border=ft.Border.all(1, "#E2E8F0"),
        padding=ft.Padding.symmetric(horizontal=20 if is_mobile else 56, vertical=28 if is_mobile else 40),
        content=ft.Column(controls, spacing=16, scroll=ft.ScrollMode.AUTO),
    )


def height(is_mobile: bool = False) -> int:
    return 830 if is_mobile else 1090
