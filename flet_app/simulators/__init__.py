"""Channel simulators — native Flet control trees showing how a post will
look on each channel before it is published.

The data shaping (which image tag goes where, how a tweet gets cut, which
photos are "extra") lives in `flet_app.simulators.base` /
`ai_workers.photo_placement`; the modules here only turn it into controls.
"""
from __future__ import annotations

import flet as ft

from flet_app.simulators import instagram, naver_blog, shorts, x_thread

CHANNELS = {
    "naver": {"label": "네이버 블로그", "icon": "📗"},
    "instagram": {"label": "인스타그램", "icon": "📸"},
    "x": {"label": "X (트위터)", "icon": "𝕏"},
    "shorts": {"label": "쇼츠 / 릴스", "icon": "🎬"},
}


# Shown instead of an account name when the brand kit has no Instagram handle.
# The previews used to fall back to a made-up handle, which read as the real
# account in every screenshot and review.
HANDLE_MISSING = "(인스타 핸들 미입력)"


def _instagram_handle(brand_kit: dict, at: bool = False) -> str:
    handle = (brand_kit.get("instagram_handle") or "").strip().lstrip("@")
    if not handle:
        return HANDLE_MISSING
    return f"@{handle}" if at else handle

def render(channel: str, campaign: dict, is_mobile: bool = False, brand_kit: dict | None = None, **kwargs) -> ft.Control:
    brand_kit = brand_kit or {}
    if channel == "naver":
        blog_name = (brand_kit.get("sub_brand") or brand_kit.get("brand_name") or "공식 블로그") + " 공식 블로그"
        return naver_blog.render(campaign, is_mobile=is_mobile, blog_name=blog_name)
    if channel == "instagram":
        return instagram.render(campaign, username=_instagram_handle(brand_kit))
    if channel == "x":
        return x_thread.render(campaign, display_name=brand_kit.get("sub_brand") or "더봄봄")
    if channel == "shorts":
        return shorts.render(
            campaign,
            handle=_instagram_handle(brand_kit, at=True),
            show_dead_zone=kwargs.get("show_dead_zone", True),
        )
    raise ValueError(f"Unknown channel: {channel}")
