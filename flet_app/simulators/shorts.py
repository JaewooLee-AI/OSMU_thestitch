"""쇼츠/릴스 시뮬레이터 — Flet 네이티브.

원본이 잡는 문제: 플레이어의 우측 버튼 클러스터·하단 자막/메타 영역이 프레임의
바깥쪽 1/4가량을 가린다는 것. 그 데드존을 실제 첨부 사진 위에 겹쳐 보여준다.
`ft.Stack`의 `top`/`left`/`right`/`bottom` 절대 위치 지정으로 원본 CSS의 좌표를
그대로 옮긴다.
"""
from __future__ import annotations

import flet as ft

from core import storage
from flet_app.simulators.base import all_images

CAPTION_SAFE_CHARS = 20
FRAME_WIDTH = 340
FRAME_HEIGHT = 604
_DEAD_ZONE_FILL = ft.Colors.with_opacity(0.28, ft.Colors.RED)
_DEAD_ZONE_BORDER = ft.Colors.with_opacity(0.55, ft.Colors.RED)


def render(campaign: dict, handle: str = "@handle", show_dead_zone: bool = True) -> ft.Control:
    script = campaign.get("shorts_script") or {}
    if isinstance(script, str):
        script = {}

    images = all_images(campaign.get("content") or "", campaign.get("storage_file_paths") or [])
    if images:
        bg: ft.Control = ft.Image(
            src=str(storage.abs_path(images[0])), width=FRAME_WIDTH, height=FRAME_HEIGHT, fit=ft.BoxFit.COVER,
        )
    else:
        bg = ft.Container(
            width=FRAME_WIDTH, height=FRAME_HEIGHT, bgcolor="#1F1F1F", alignment=ft.Alignment.CENTER,
            content=ft.Text("첨부 사진이 없어 배경을 미리 볼 수 없습니다", size=13, color="#555555"),
        )

    hook = (script.get("hook") or "").strip()
    title = (script.get("title") or campaign.get("title") or "").strip()
    scenes = script.get("scenes") or []
    hashtags = script.get("hashtags") or []

    stack_children: list[ft.Control] = [bg]

    if show_dead_zone:
        stack_children += [
            ft.Container(width=68, height=280, right=0, bottom=96, bgcolor=_DEAD_ZONE_FILL, border=ft.Border.all(1, _DEAD_ZONE_BORDER)),
            ft.Container(width=FRAME_WIDTH, height=150, left=0, bottom=0, bgcolor=_DEAD_ZONE_FILL, border=ft.Border.all(1, _DEAD_ZONE_BORDER)),
            ft.Container(
                content=ft.Text("BUTTONS", size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                bgcolor="#DC2626", padding=ft.Padding.symmetric(horizontal=6, vertical=1), border_radius=3,
                right=2, bottom=230,
            ),
            ft.Container(
                content=ft.Text("TEXT & META", size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                bgcolor="#DC2626", padding=ft.Padding.symmetric(horizontal=6, vertical=1), border_radius=3,
                left=12, bottom=142,
            ),
            ft.Container(left=16, right=84, top=84, bottom=158, border=ft.Border.all(1, "#86C1B3"), border_radius=6),
            ft.Container(
                content=ft.Text("SAFE AREA", size=9, weight=ft.FontWeight.BOLD, color="#0F172A"),
                bgcolor="#86C1B3", padding=ft.Padding.symmetric(horizontal=6, vertical=1), border_radius=3,
                left=16, top=76,
            ),
        ]

    if hook:
        stack_children.append(
            ft.Container(
                content=ft.Text(hook, size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE, text_align=ft.TextAlign.CENTER),
                left=24, right=92, top=120,
            )
        )

    stack_children.append(
        ft.Container(
            left=0, right=0, bottom=0,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.BOTTOM_CENTER, end=ft.Alignment.TOP_CENTER,
                colors=[ft.Colors.with_opacity(0.9, ft.Colors.BLACK), ft.Colors.with_opacity(0, ft.Colors.BLACK)],
            ),
            padding=ft.Padding.only(left=14, right=14, top=16, bottom=24),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(width=30, height=30, border_radius=15, bgcolor=ft.Colors.WHITE),
                            ft.Text(handle, size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                            ft.Container(
                                content=ft.Text("구독", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK),
                                bgcolor=ft.Colors.WHITE, border_radius=999,
                                padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(title, size=14, color=ft.Colors.WHITE, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=8,
            ),
        )
    )

    frame = ft.Container(
        width=FRAME_WIDTH, height=FRAME_HEIGHT, border_radius=24,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS, bgcolor="#0F0F0F",
        content=ft.Stack(stack_children, width=FRAME_WIDTH, height=FRAME_HEIGHT),
    )

    if scenes:
        cut_controls: list[ft.Control] = [
            ft.Text(title or "쇼츠 구성안", weight=ft.FontWeight.BOLD, size=14, color="#0F172A"),
            ft.Text("자막은 위 SAFE AREA(초록 테두리) 안에 들어가야 합니다.", size=11, color="#64748B"),
        ]
        for i, scene in enumerate(scenes, start=1):
            caption = (scene.get("caption") or "").strip()
            cut_controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(f"CUT {i}", size=11, weight=ft.FontWeight.BOLD, color="#A6224B"),
                            ft.Text(caption or "—", size=14, weight=ft.FontWeight.BOLD, color="#0F172A"),
                            ft.Text(scene.get("shot") or "", size=12, color="#64748B"),
                            *(
                                [ft.Text(
                                    f"⚠️ 자막 {len(caption)}자 — {CAPTION_SAFE_CHARS}자를 넘으면 세로 화면에서 "
                                    "두 줄로 깨지거나 UI에 가립니다.",
                                    size=11, color="#B91C1C",
                                )]
                                if len(caption) > CAPTION_SAFE_CHARS else []
                            ),
                        ],
                        spacing=3,
                    ),
                    border=ft.Border(left=ft.BorderSide(3, "#A6224B")),
                    padding=ft.Padding.only(left=12, top=8, bottom=8),
                )
            )
        if hashtags:
            cut_controls.append(ft.Text(" ".join(hashtags), size=12, color="#2B4C8C"))
        script_panel: ft.Control = ft.Container(
            width=420, bgcolor=ft.Colors.WHITE, border=ft.Border.all(1, "#E2E8F0"), border_radius=12,
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            content=ft.Column(cut_controls, spacing=10),
        )
    else:
        script_panel = ft.Text("쇼츠 구성안이 아직 생성되지 않았습니다.", size=13, color="#64748B")

    return ft.Column(
        [frame, script_panel], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=16,
        scroll=ft.ScrollMode.AUTO,
    )


def height() -> int:
    return 1080
