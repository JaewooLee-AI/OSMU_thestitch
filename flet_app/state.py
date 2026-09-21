"""Minimal shared state for cross-view handoffs and app-wide settings.

Streamlit's `st.session_state["wb_campaign_id"]` implicitly threads a
selected campaign id from the News Curation page to the Workbench page —
any page can read/write it, so the coupling isn't visible at either call
site. Flet has no such implicit global. This is the explicit stand-in: one
instance created in flet_app/main.py and passed to every view's `build()`
call, so a future news_curation_view can set
`state.pending_workbench_campaign_id` and a future workbench_view can read
and clear it on load.

`font_scale` + `request_rerender` back the ⚙️ 설정 화면의 글자 크기 조정 기능.
Views are built fresh from `core.repo` state every time a nav destination is
selected (see flet_app/main.py's `show()`), so "apply the new scale" is just
"rebuild the current view and the page theme" — `request_rerender` is that
callback, wired up by main.py once the page/rail exist.

`navigate_to_workbench` lets a view other than the workbench itself trigger
the nav-rail switch programmatically — e.g. news_curation_view's "뉴스 기반
콘텐츠" cards jumping straight to a specific campaign in the workbench,
instead of only handing off data and making the marketer find the campaign
in the dropdown themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from flet_app.theme import DEFAULT_FONT_SCALE

# core.repo.get_app_state/set_app_state 키 — main.py(초기 로드)와
# settings_view.py(저장) 양쪽이 같은 문자열을 써야 하므로 여기 하나에 둔다.
FONT_SCALE_STATE_KEY = "ui_font_scale"


@dataclass
class AppState:
    pending_workbench_campaign_id: str | None = None
    font_scale: float = DEFAULT_FONT_SCALE
    request_rerender: Optional[Callable[[], None]] = None
    navigate_to_workbench: Optional[Callable[[], None]] = None
