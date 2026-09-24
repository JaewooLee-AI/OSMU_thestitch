"""브랜드 킷.

Business rules (carried over from the original Streamlit screen):
- a blank SEO-keyword weight cell means "no opinion yet" -> neutral 1.0, not 0
- a blank "검색 타깃" checkbox means still included, not excluded
- a keyword-competitiveness freshness warning must stay visible, since
  expiry silently turns off conversion weighting (ai_workers.keyword_research)
- the brand kit is pre-seeded on first launch (core/brand_seed.py), so this
  view never actually starts from an empty state
"""
from __future__ import annotations

import flet as ft

from ai_workers import content_mode, keyword_research
from core import repo

from flet_app.components.editable_table import ColumnSpec, EditableTable
from flet_app.state import AppState
from flet_app.theme import BRAND_COLORS, fs, palette_row

FEW_SHOT_DELIMITER = "\n---\n"


def build(page: ft.Page, state: AppState) -> ft.Control:
    scale = state.font_scale
    existing = repo.get_brand_kit()

    # --- 기업 · 채널 ---------------------------------------------------------
    brand_name = ft.TextField(label="기업명", value=existing.get("brand_name") or "", expand=True)
    sub_brand = ft.TextField(label="브랜드명", value=existing.get("sub_brand") or "", expand=True)
    industry = ft.TextField(label="업종", value=existing.get("industry") or "", expand=True)
    homepage = ft.TextField(label="홈페이지", value=existing.get("homepage") or "", expand=True)
    naver_blog_id = ft.TextField(
        label="네이버 블로그 아이디",
        value=existing.get("naver_blog_id") or "",
        helper="blog.naver.com/<아이디>. 반자동 게시가 이 블로그로 글을 올립니다.",
        expand=True,
    )
    instagram_handle = ft.TextField(
        label="인스타그램 핸들", value=existing.get("instagram_handle") or "", expand=True
    )

    guardrail_enabled = ft.Switch(
        label="🛡️ 환경성 표시·광고 컴플라이언스 가드레일",
        value=bool(existing.get("guardrail_enabled", True)),
    )

    persona = ft.TextField(
        label="브랜드 페르소나",
        value=existing.get("persona") or "",
        multiline=True,
        min_lines=4,
        max_lines=8,
        expand=True,
    )
    tone_and_manner = ft.TextField(
        label="톤앤매너 가이드",
        value=existing.get("tone_and_manner") or "",
        multiline=True,
        min_lines=6,
        max_lines=12,
        expand=True,
    )

    # --- 핵심 팩트 ------------------------------------------------------------
    core_facts_field = ft.TextField(
        label="핵심 팩트 (한 줄에 하나씩)",
        value="\n".join(existing.get("core_facts") or []),
        multiline=True,
        min_lines=8,
        max_lines=16,
        expand=True,
    )

    # --- 용어집 ---------------------------------------------------------------
    terminology = existing.get("terminology") or {}
    terminology_table = EditableTable(
        columns=[
            ColumnSpec("term", "용어", "text"),
            ColumnSpec("desc", "설명/올바른 표기", "text"),
        ],
        rows=[{"term": k, "desc": v} for k, v in terminology.items()],
    )

    # --- 기본 콘텐츠 모드 -------------------------------------------------------
    mode_keys = content_mode.ORDER
    saved_mode = existing.get("default_content_mode") or content_mode.DEFAULT_MODE
    mode_dropdown = ft.Dropdown(
        label="기본 콘텐츠 모드",
        expand=True,
        value=saved_mode if saved_mode in mode_keys else content_mode.DEFAULT_MODE,
        options=[
            ft.DropdownOption(key=k, text=f"{content_mode.MODES[k]['icon']} {content_mode.MODES[k]['label']}")
            for k in mode_keys
        ],
    )
    mode_captions = ft.Column(
        [
            ft.Text(
                f"· {content_mode.MODES[k]['icon']} {content_mode.MODES[k]['label']} — "
                f"{content_mode.describe(k)}",
                size=fs(12, scale),
                color=BRAND_COLORS["text_muted"],
            )
            for k in mode_keys
        ]
    )

    # --- SEO 키워드 -----------------------------------------------------------
    keyword_weights = existing.get("keyword_weights") or {}
    non_target = set(existing.get("non_target_keywords") or [])
    keywords_table = EditableTable(
        columns=[
            ColumnSpec("keyword", "키워드", "text"),
            ColumnSpec("weight", "전환 가중치", "number", default=1.0, width=90),
            ColumnSpec("target", "검색 타깃", "checkbox", default=True),
        ],
        rows=[
            {
                "keyword": k,
                "weight": float(keyword_weights.get(k, 1.0)),
                "target": k not in non_target,
            }
            for k in (existing.get("seo_keywords") or [])
        ],
    )

    # 만료는 조용히 일어나고, 조용히 가중치 체계를 끕니다 — keyword_research.pool_freshness 참고.
    freshness = keyword_research.pool_freshness(existing.get("seo_keywords") or [])
    freshness_banner: ft.Control | None = None
    if freshness["stale"]:
        stale = freshness["stale"]
        freshness_banner = ft.Container(
            content=ft.Text(
                f"📉 경쟁도 측정이 만료된 키워드 {len(stale)}개: "
                f"{', '.join(stale[:6])}{'…' if len(stale) > 6 else ''}\n"
                "만료된 키워드는 전환 가중치가 적용되지 않고, 초안에 몇 번 나왔는지로만 순위가 "
                "정해집니다. ⚙️ 설정 페이지에서 재측정하세요.",
                color="#B3261E",
                size=fs(12, scale),
            ),
            bgcolor="#FDECEA",
            padding=10,
            border_radius=8,
        )
    elif freshness["days_left"] is not None:
        freshness_banner = ft.Text(
            f"📈 경쟁도 측정: 가장 오래된 것이 {freshness['oldest_document_age']:.0f}일 전 · "
            f"{freshness['days_left']:.0f}일 후 만료됩니다. 만료되면 전환 가중치가 조용히 꺼지므로, "
            "그 전에 ⚙️ 설정에서 재측정하세요.",
            size=fs(12, scale),
            color=BRAND_COLORS["text_muted"],
        )

    # --- 금기어 사전 -----------------------------------------------------------
    blacklist_map = existing.get("blacklist_map") or {}
    blacklist_table = EditableTable(
        columns=[
            ColumnSpec("bad", "금기어", "text"),
            ColumnSpec("good", "치환어", "text"),
        ],
        rows=[{"bad": k, "good": v} for k, v in blacklist_map.items()],
    )

    # --- Few-shot 샘플 ----------------------------------------------------------
    few_shot_field = ft.TextField(
        label="우수 포스팅 샘플 (여러 개면 빈 줄에 --- 로 구분)",
        value=FEW_SHOT_DELIMITER.join(existing.get("few_shot_samples") or []),
        multiline=True,
        min_lines=8,
        max_lines=16,
        expand=True,
    )

    # --- 저장 --------------------------------------------------------------
    save_status = ft.Text("", size=fs(12, scale), color="#1B6E3C")
    guardrail_warning_box = ft.Container(visible=not bool(existing.get("guardrail_enabled", True)))
    guardrail_warning_box.content = ft.Text(
        "가드레일이 꺼져 있습니다. 검증되지 않은 환경성 주장이 검수 없이 발행될 수 있습니다.",
        color="#B3261E",
        size=fs(12, scale),
    )
    guardrail_warning_box.bgcolor = "#FDECEA"
    guardrail_warning_box.padding = 10
    guardrail_warning_box.border_radius = 8

    def on_save(e: ft.Event) -> None:
        core_facts = [line.strip() for line in (core_facts_field.value or "").splitlines() if line.strip()]
        few_shot_samples = [
            s.strip() for s in (few_shot_field.value or "").split(FEW_SHOT_DELIMITER.strip()) if s.strip()
        ]

        new_terminology = {
            row["term"]: row["desc"] for row in terminology_table.get_rows() if row["term"]
        }
        new_blacklist = {
            row["bad"]: row["good"] for row in blacklist_table.get_rows() if row["bad"]
        }

        new_keywords: list[str] = []
        new_weights: dict[str, float] = {}
        new_non_targets: list[str] = []
        for row in keywords_table.get_rows():
            kw = row["keyword"]
            if not kw or kw in new_weights:
                continue
            new_keywords.append(kw)
            new_weights[kw] = row["weight"]
            if not row["target"]:
                new_non_targets.append(kw)

        repo.save_brand_kit(
            brand_name=brand_name.value,
            sub_brand=sub_brand.value,
            industry=industry.value,
            homepage=homepage.value,
            naver_blog_id=naver_blog_id.value,
            instagram_handle=instagram_handle.value,
            guardrail_enabled=guardrail_enabled.value,
            persona=persona.value,
            tone_and_manner=tone_and_manner.value,
            core_facts=core_facts,
            terminology=new_terminology,
            seo_keywords=new_keywords,
            keyword_weights=new_weights,
            non_target_keywords=new_non_targets,
            default_content_mode=mode_dropdown.value,
            blacklist_map=new_blacklist,
            few_shot_samples=few_shot_samples,
        )

        guardrail_warning_box.visible = not guardrail_enabled.value
        save_status.value = (
            f"✅ 저장했습니다. (핵심 팩트 {len(core_facts)} · 용어 {len(new_terminology)} · "
            f"SEO 키워드 {len(new_keywords)} · 금기어 {len(new_blacklist)} · 샘플 {len(few_shot_samples)})"
        )
        page.update()

    save_button = ft.FilledButton("저장", icon=ft.Icons.SAVE, on_click=on_save)

    # --- 제목 반복 방지 (Streamlit에선 "폼 밖" 제약이 있었지만 Flet엔 없음) -------
    history_count = repo.title_history_count()
    history_list = ft.Column(
        [ft.Text(f"• {t}", size=fs(12, scale)) for t in repo.recent_titles(50)],
        scroll=ft.ScrollMode.AUTO,
        height=200,
        visible=False,
    )

    def toggle_history(e: ft.Event) -> None:
        history_list.visible = not history_list.visible
        page.update()

    def clear_history(e: ft.Event) -> None:
        repo.clear_title_history()
        history_list.controls = []
        save_status.value = "제목 이력을 삭제했습니다."
        page.update()

    history_section = ft.Column(
        [
            ft.Text("🎲 제목 반복 방지", weight=ft.FontWeight.BOLD),
            ft.Text(
                f"지금까지 생성한 제목 {history_count}건을 기억해, 새 제목이 과거 제목과 문장 구조까지 "
                "겹치면 자동으로 다시 짓습니다. 이 이력은 캠페인을 삭제해도 남습니다.",
                size=fs(12, scale),
                color=BRAND_COLORS["text_muted"],
            ),
            ft.Row(
                [
                    ft.TextButton(f"이력 {history_count}건 보기/숨기기", on_click=toggle_history),
                    ft.TextButton("🗑️ 제목 이력 전체 삭제", on_click=clear_history),
                ]
            ),
            history_list,
        ]
    )

    return ft.Column(
        [
            ft.Text("브랜드 킷", size=fs(24, scale), weight=ft.FontWeight.BOLD),
            ft.Text(
                "여기 등록한 내용은 매 생성마다 프롬프트에 그대로 주입됩니다 (유사도 검색이 아니라 전체 주입).",
                size=fs(12, scale),
                color=BRAND_COLORS["text_muted"],
            ),
            ft.Divider(),
            ft.Text("🎨 브랜드 컬러", weight=ft.FontWeight.BOLD),
            palette_row(scale=scale),
            ft.Divider(),
            ft.Text("🏢 기업 · 채널", weight=ft.FontWeight.BOLD),
            ft.Row([brand_name, sub_brand]),
            industry,
            ft.Row([homepage, naver_blog_id, instagram_handle]),
            ft.Divider(),
            guardrail_enabled,
            persona,
            tone_and_manner,
            ft.Divider(),
            ft.Text("🏭 회사 핵심 팩트", weight=ft.FontWeight.BOLD),
            core_facts_field,
            ft.Divider(),
            ft.Text("📖 회사 용어집", weight=ft.FontWeight.BOLD),
            terminology_table.build(),
            ft.Divider(),
            mode_dropdown,
            mode_captions,
            ft.Divider(),
            ft.Text("🔍 SEO 키워드 (네이버 블로그 전용)", weight=ft.FontWeight.BOLD),
            keywords_table.build(),
            *([freshness_banner] if freshness_banner else []),
            ft.Divider(),
            ft.Text("🚫 금기어 치환 사전 (그린워싱 방지)", weight=ft.FontWeight.BOLD),
            blacklist_table.build(),
            ft.Divider(),
            few_shot_field,
            ft.Row([save_button, save_status]),
            guardrail_warning_box,
            ft.Divider(),
            history_section,
        ],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        spacing=10,
    )
