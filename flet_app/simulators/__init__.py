"""Channel simulators — Flet-native rewrite of the top-level `simulators/`
package.

`simulators/*.py` builds a complete HTML document for `st.components.v1.html`
(a sandboxed iframe). Flet's WebView doesn't support Windows, so that iframe
approach can't be reused there — this package renders the same information
as native Flet control trees instead. The actual *data* shaping (which image
tag goes where, how a caption/tweet gets cut, which photos are "extra") is
unchanged and still lives in `simulators.base` / `ai_workers.photo_placement`;
only the "turn that into markup" step is rewritten here.
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


def render(channel: str, campaign: dict, is_mobile: bool = False, brand_kit: dict | None = None, **kwargs) -> ft.Control:
    brand_kit = brand_kit or {}
    if channel == "naver":
        blog_name = (brand_kit.get("sub_brand") or brand_kit.get("brand_name") or "공식 블로그") + " 공식 블로그"
        return naver_blog.render(campaign, is_mobile=is_mobile, blog_name=blog_name)
    if channel == "instagram":
        return instagram.render(campaign, username=brand_kit.get("instagram_handle") or "thestitch_artplay")
    if channel == "x":
        return x_thread.render(campaign, display_name=brand_kit.get("sub_brand") or "더봄봄")
    if channel == "shorts":
        return shorts.render(
            campaign,
            handle="@" + (brand_kit.get("instagram_handle") or "thestitch_artplay"),
            show_dead_zone=kwargs.get("show_dead_zone", True),
        )
    raise ValueError(f"Unknown channel: {channel}")
