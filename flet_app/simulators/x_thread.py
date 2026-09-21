"""X(트위터) 타래 시뮬레이터 — Flet 네이티브.

문자열 분할 알고리즘(`_split_tweet`)은 프레임워크와 무관한 순수 로직이라
원본 `simulators/x_thread.py`에서 그대로 가져다 쓴다 — 여기서 새로 짜는 건
그 결과를 HTML 대신 네이티브 컨트롤로 그리는 부분뿐이다. 미디어 그리드(1~4장의
4가지 배치)도 원본과 동일한 레이아웃 규칙을 따른다.
"""
from __future__ import annotations

from typing import List

import flet as ft

from core import storage
from simulators.base import all_images
from simulators.x_thread import MAX_MEDIA, TWEET_LIMIT, _split_tweet  # noqa: F401 (재사용)

from flet_app.theme import BRAND_COLORS

MEDIA_WIDTH = 552
MEDIA_HEIGHT = round(MEDIA_WIDTH * 9 / 16)


def _img(path: str, w: float, h: float) -> ft.Image:
    return ft.Image(src=str(storage.abs_path(path)), width=w, height=h, fit=ft.BoxFit.COVER)


def _media_grid(images: List[str]) -> ft.Control | None:
    count = min(len(images), MAX_MEDIA)
    if count == 0:
        return None
    gap = 2
    if count == 1:
        content: ft.Control = _img(images[0], MEDIA_WIDTH, MEDIA_HEIGHT)
    elif count == 2:
        w = (MEDIA_WIDTH - gap) / 2
        content = ft.Row([_img(images[0], w, MEDIA_HEIGHT), _img(images[1], w, MEDIA_HEIGHT)], spacing=gap)
    elif count == 3:
        w_half = (MEDIA_WIDTH - gap) / 2
        h_half = (MEDIA_HEIGHT - gap) / 2
        content = ft.Row(
            [
                _img(images[0], w_half, MEDIA_HEIGHT),
                ft.Column([_img(images[1], w_half, h_half), _img(images[2], w_half, h_half)], spacing=gap),
            ],
            spacing=gap,
        )
    else:
        w_half = (MEDIA_WIDTH - gap) / 2
        h_half = (MEDIA_HEIGHT - gap) / 2
        content = ft.Column(
            [
                ft.Row([_img(images[0], w_half, h_half), _img(images[1], w_half, h_half)], spacing=gap),
                ft.Row([_img(images[2], w_half, h_half), _img(images[3], w_half, h_half)], spacing=gap),
            ],
            spacing=gap,
        )
    return ft.Container(
        content=content, border_radius=16, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border=ft.Border.all(1, "#CFD9DE"),
    )


def render(campaign: dict, display_name: str = "브랜드", handle: str = "@handle") -> ft.Control:
    tweets: List[str] = campaign.get("x_content") or []
    hashtags = campaign.get("x_hashtags") or []
    images = all_images(campaign.get("content") or "", campaign.get("storage_file_paths") or [])

    chunks: List[str] = []
    for tweet in tweets:
        chunks.extend(_split_tweet(tweet))

    if not chunks:
        return ft.Container(
            width=600, height=200, alignment=ft.Alignment.CENTER,
            border=ft.Border.all(1, "#EFF3F4"),
            content=ft.Text("X 스레드가 아직 생성되지 않았습니다.", color="#536471", size=14),
        )

    if hashtags:
        chunks[-1] = chunks[-1] + "\n\n" + " ".join(hashtags)

    rows: list[ft.Control] = []
    for idx, chunk in enumerate(chunks):
        media = _media_grid(images) if idx == 0 else None
        rail = ft.Column(
            [
                ft.Container(
                    width=40, height=40, border_radius=20,
                    gradient=ft.LinearGradient(
                        begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                        colors=[BRAND_COLORS["primary"], BRAND_COLORS["secondary"]],
                    ),
                ),
                *([ft.Container(width=2, height=24, bgcolor="#CFD9DE")] if idx != len(chunks) - 1 else []),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        )
        main = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(display_name, weight=ft.FontWeight.BOLD, size=15, color="#0F1419"),
                        ft.Text(handle, size=15, color="#536471"),
                        ft.Text(f"· {idx + 1}분", size=15, color="#536471"),
                    ],
                    spacing=4,
                ),
                ft.Text(chunk, size=15, color="#0F1419"),
                *([media] if media else []),
                ft.Row(
                    [
                        ft.Text("💬 12", size=13, color="#536471"),
                        ft.Text("🔁 8", size=13, color="#536471"),
                        ft.Text("♡ 46", size=13, color="#536471"),
                        ft.Text("↥", size=13, color="#536471"),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    width=320,
                ),
            ],
            spacing=6,
            expand=True,
        )
        rows.append(
            ft.Container(
                content=ft.Row([rail, main], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
                padding=ft.Padding.only(left=16, right=16, top=12, bottom=8),
                border=ft.Border(bottom=ft.BorderSide(1, "#EFF3F4")),
            )
        )

    warning = None
    if len(images) > MAX_MEDIA:
        warning = ft.Container(
            content=ft.Text(
                f"사진 {len(images)}장 중 X는 첫 {MAX_MEDIA}장만 첨부됩니다. "
                f"나머지 {len(images) - MAX_MEDIA}장은 표시되지 않습니다.",
                size=12, color="#C05621",
            ),
            bgcolor="#FFFAF0", border=ft.Border.all(1, "#FBD38D"), border_radius=8, padding=8,
            margin=ft.Margin.symmetric(horizontal=16, vertical=8),
        )

    return ft.Container(
        width=600, height=860, bgcolor=ft.Colors.WHITE, border=ft.Border.all(1, "#EFF3F4"),
        content=ft.Column(([warning] if warning else []) + rows, spacing=0, scroll=ft.ScrollMode.AUTO),
    )


def height() -> int:
    return 860
