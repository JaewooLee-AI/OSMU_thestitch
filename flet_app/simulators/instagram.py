"""인스타그램 피드 시뮬레이터 — Flet 네이티브.

원본이 잡는 핵심 문제는 캡션의 125자 "더보기" 폴드 — 그 지점을 텍스트 분할로
그대로 표시한다. 캐러셀은 Flet에 스와이프 가능한 위젯이 없어 첫 장만 보여주고
"N장 중 1번째"로 단순화했다 — 장수·경고는 원본과 동일하게 유지한다.
"""
from __future__ import annotations

import flet as ft

from core import storage
from simulators.base import all_images

TRUNCATE_LENGTH = 125
MAX_CAROUSEL = 10


def render(campaign: dict, username: str = "thestitch_artplay") -> ft.Control:
    caption = campaign.get("instagram_caption") or ""
    hashtags = campaign.get("instagram_hashtags") or []
    images = all_images(campaign.get("content") or "", campaign.get("storage_file_paths") or [])

    media_note = None
    if images:
        media = ft.Image(src=str(storage.abs_path(images[0])), width=390, height=487, fit=ft.BoxFit.COVER)
        if len(images) > 1:
            media_note = ft.Text(
                f"사진 {len(images)}장 중 1번째 — 실제로는 좌우 스와이프 캐러셀입니다.",
                size=10, color="#8E8E8E",
            )
    else:
        media = ft.Container(
            width=390, height=487, bgcolor="#FAFAFA", alignment=ft.Alignment.CENTER,
            content=ft.Text("사진을 첨부하면 4:5 캐러셀로 미리 보입니다", color="#B0B0B0", size=13),
        )

    overflow_warning = None
    if len(images) > MAX_CAROUSEL:
        overflow_warning = ft.Container(
            content=ft.Text(
                f"사진 {len(images)}장 중 인스타그램은 {MAX_CAROUSEL}장까지만 올릴 수 있습니다. "
                f"뒤의 {len(images) - MAX_CAROUSEL}장은 잘립니다.",
                size=11, color="#C05621",
            ),
            bgcolor="#FFFAF0", border=ft.Border.all(1, "#FBD38D"), border_radius=6, padding=8,
            margin=ft.Margin.symmetric(horizontal=12, vertical=4),
        )

    if len(caption) > TRUNCATE_LENGTH:
        head, tail = caption[:TRUNCATE_LENGTH], caption[TRUNCATE_LENGTH:]
        caption_block = ft.Column(
            [
                ft.Text(
                    spans=[
                        ft.TextSpan(username + "  ", style=ft.TextStyle(weight=ft.FontWeight.W_600)),
                        ft.TextSpan(head),
                        ft.TextSpan(" ... 더보기", style=ft.TextStyle(color="#8E8E8E")),
                    ],
                    size=13,
                ),
                ft.Container(
                    content=ft.Text("▲ 여기까지만 노출됩니다 (125자)", size=10, color="#EE2A7B"),
                    border=ft.Border(top=ft.BorderSide(1, "#EE2A7B")),
                    padding=ft.Padding.only(top=3),
                ),
                ft.Text(tail, size=13, color="#262626"),
            ],
            spacing=4,
        )
    elif caption:
        caption_block = ft.Text(
            spans=[
                ft.TextSpan(username + "  ", style=ft.TextStyle(weight=ft.FontWeight.W_600)),
                ft.TextSpan(caption),
            ],
            size=13,
        )
    else:
        caption_block = ft.Text("캡션이 아직 생성되지 않았습니다.", size=13, color="#B0B0B0")

    tags_line = ft.Text(" ".join(hashtags), size=13, color="#00376B") if hashtags else None

    header = ft.Row(
        [
            ft.Row(
                [
                    ft.Container(
                        width=32, height=32, border_radius=16,
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                            colors=["#F9CE34", "#EE2A7B", "#6228D7"],
                        ),
                    ),
                    ft.Text(username, size=13, weight=ft.FontWeight.W_600, color="#262626"),
                ],
                spacing=8,
            ),
            ft.Text("···", weight=ft.FontWeight.BOLD, color="#262626"),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    icons_row = ft.Row(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.FAVORITE_BORDER, size=24, color="#262626"),
                    ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=24, color="#262626"),
                    ft.Icon(ft.Icons.SEND_OUTLINED, size=24, color="#262626"),
                ],
                spacing=16,
            ),
            ft.Icon(ft.Icons.BOOKMARK_BORDER, size=24, color="#262626"),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    body: list[ft.Control] = [
        ft.Container(
            content=header, padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border=ft.Border(bottom=ft.BorderSide(1, "#F1F1F1")),
        ),
        media,
    ]
    if media_note:
        body.append(ft.Container(content=media_note, padding=ft.Padding.symmetric(horizontal=12, vertical=4)))
    if overflow_warning:
        body.append(overflow_warning)
    body.append(ft.Container(content=icons_row, padding=ft.Padding.only(left=12, right=12, top=10, bottom=6)))
    body.append(ft.Container(
        content=ft.Column([caption_block] + ([tags_line] if tags_line else []), spacing=6),
        padding=ft.Padding.symmetric(horizontal=12),
        margin=ft.Margin.only(bottom=16),
    ))

    return ft.Container(
        width=390, height=780, bgcolor=ft.Colors.WHITE, border=ft.Border.all(1, "#DBDBDB"),
        content=ft.Column(body, spacing=0, scroll=ft.ScrollMode.AUTO),
    )


def height() -> int:
    return 830
