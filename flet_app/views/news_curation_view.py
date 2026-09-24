"""뉴스 큐레이션.

이 화면은 기사를 "고르기"만 한다 — 실제 생성은 워크벤치에서 사진·메모를 붙인
뒤 일어난다(같은 파이프라인을 news/manual이 공유하는 이유는
ai_workers/content_writer.py 참고). "워크벤치로 보내기"를 누르면
`state.pending_workbench_campaign_id`에 새 캠페인 id를 심어두는데, 이건
`flet_app/views/workbench_view.py`가 처음 만들어질 때부터 준비해둔 자리다 —
워크벤치 화면으로 이동하면 그 캠페인이 자동으로 선택된다.
"""
from __future__ import annotations

import time

import flet as ft

from ai_workers import keyword_research
from ai_workers.news_search import search_news_by_keywords
from core import repo

from flet_app.components.collapsible import collapsible
from flet_app.state import AppState
from flet_app.theme import BRAND_COLORS, fs, status_chip


def _volume_of(keyword: str) -> float:
    cached = repo.get_cached_metric(
        keyword_research.normalize(keyword), "ad_volume", keyword_research.VOLUME_CACHE_DAYS
    )
    try:
        return float(cached) if cached is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def build(page: ft.Page, state: AppState) -> ft.Control:
    scale = state.font_scale
    brand_kit = repo.get_brand_kit()
    seo_keywords = brand_kit.get("seo_keywords") or []
    # 검색량 상위 3개를 기본 선택 — 뉴스가 실제로 걸릴 확률은 검색량과 같이 간다.
    default_keywords = set(sorted(seo_keywords, key=_volume_of, reverse=True)[:3])

    controls: list[ft.Control] = [
        ft.Text("뉴스 큐레이션", size=fs(24, scale), weight=ft.FontWeight.BOLD),
        ft.Text(
            "브랜드 키워드로 기사를 찾아 워크벤치로 보냅니다. 사진과 메모를 붙인 뒤 초안을 생성하면 "
            "뉴스를 우리 브랜드 관점으로 재해석한 글이 나옵니다.",
            size=fs(12, scale), color=BRAND_COLORS["text_muted"],
        ),
    ]

    if not seo_keywords:
        controls.append(ft.Container(
            content=ft.Text(
                "브랜드 킷에 SEO 키워드가 없습니다. 아래에 직접 검색어를 입력하거나 먼저 키워드를 등록하세요.",
                size=fs(12, scale), color="#2F6B7A",
            ),
            bgcolor="#E7F1F3", padding=10, border_radius=8,
        ))

    # --- 키워드 선택 (칩) -------------------------------------------------------
    # Chip의 selected_color(네이비)가 켜지면 라벨 기본 글자색(어두운 색)과 겹쳐
    # 안 보이게 된다 — 선택 상태에 따라 라벨 색을 직접 흰색/어두운 색으로 바꿔야 한다.
    keyword_selected: dict[str, bool] = {kw: kw in default_keywords for kw in seo_keywords}
    keyword_chips: list[ft.Chip] = []
    for kw in seo_keywords:
        is_selected = keyword_selected[kw]
        label_text = ft.Text(kw, color=ft.Colors.WHITE if is_selected else BRAND_COLORS["text"])
        chip = ft.Chip(
            label=label_text, selected=is_selected, show_checkmark=True,
            check_color=ft.Colors.WHITE,
            selected_color=BRAND_COLORS["primary"],
            bgcolor=ft.Colors.WHITE,
            border_side=ft.BorderSide(1, "#D8D0C4"),
        )

        def on_select(e: ft.Event, kw: str = kw, chip: ft.Chip = chip, label_text: ft.Text = label_text) -> None:
            # Flet의 on_select는 호출되기 전에 이미 chip.selected를 뒤집어 놓는다 —
            # 여기서 또 뒤집으면 원래 값으로 되돌아가 "클릭해도 안 바뀌는" 것처럼 보인다.
            keyword_selected[kw] = chip.selected
            label_text.color = ft.Colors.WHITE if chip.selected else BRAND_COLORS["text"]
            chip.update()

        chip.on_select = on_select
        keyword_chips.append(chip)

    extra_field = ft.TextField(
        label="추가 검색어 (쉼표로 구분)",
        hint_text="예: 새활용 산업, 펫 휴머니제이션, 텀블벅 친환경", expand=True,
    )
    limit_field = ft.Slider(min=1, max=5, divisions=4, value=2, label="{value}건")

    results_box = ft.Container()
    search_status = ft.Text("", size=fs(12, scale))

    def render_results(results: list[dict]) -> None:
        if not results:
            results_box.content = ft.Text(
                "검색 결과가 없거나, 이미 모두 큐에 등록된 기사입니다.",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            )
            results_box.update()
            return

        items: list[ft.Control] = [
            ft.Text(f"검색 결과 {len(results)}건 (이미 등록된 기사는 자동 제외)", size=fs(12, scale), color=BRAND_COLORS["text_muted"]),
        ]
        for article in results:
            item_status = ft.Text("", size=fs(11, scale), color="#1B6E3C")

            def on_queue(e: ft.Event, article: dict = article, item_status: ft.Text = item_status) -> None:
                # 원문 헤드라인은 source_title에만 남긴다 — 네이버는 블로그·뉴스를 같은
                # 유사문서 판정 대상으로 보므로, 원문 제목을 그대로 쓰면 원본에 밀려
                # 검색에서 빠진다. AI가 기사 내용을 읽고 브랜드 관점의 제목을 새로 짓는다.
                created = repo.insert_campaign(
                    source_type="news", source_url=article["url"], source_title=article["title"],
                    status="awaiting_media",
                )
                state.pending_workbench_campaign_id = created["id"]
                item_status.value = "✅ 워크벤치로 보냈습니다. 사진과 메모를 붙인 뒤 초안을 생성하세요."
                item_status.update()

            body = ft.Column(
                [
                    *([ft.Text(article["summary"], size=fs(12, scale))] if article.get("summary") else []),
                    ft.Text(f"검색 키워드: {article.get('matched_keyword', '-')}", size=fs(11, scale), color=BRAND_COLORS["text_muted"]),
                    ft.TextButton(
                        content=ft.Text(article["url"], size=fs(10, scale), color=BRAND_COLORS["secondary"]),
                        icon=ft.Icons.OPEN_IN_NEW, icon_color=BRAND_COLORS["secondary"],
                        url=article["url"], tooltip="원문 기사 열기",
                    ),
                    ft.FilledButton("➕ 워크벤치로 보내기", on_click=on_queue),
                    item_status,
                ],
                spacing=6,
            )
            items += collapsible(f"📌 [{article['source']}] {article['title']}", body)

        results_box.content = ft.Column(items, spacing=8)
        results_box.update()

    SEARCH_IDLE_TEXT = "🔍 뉴스 검색"
    search_button = ft.FilledButton(SEARCH_IDLE_TEXT)
    # 버튼 옆 16px 스피너는 시선이 결과 목록 쪽으로 이미 옮겨간 뒤에는 놓치기
    # 쉽다 — 버튼 자체의 글자를 바꾸고, 배경색 있는 배너를 검색창 바로 아래
    # 눈에 띄는 자리에 둬서 "지금 뭔가 진행 중"이라는 신호를 이중으로 준다.
    search_spinner = ft.ProgressRing(width=18, height=18, stroke_width=3, visible=False)
    search_status_banner = ft.Container(visible=False, border_radius=8, padding=10)

    def on_search(e: ft.Event):
        # 이 함수는 (yield가 있어서) 제너레이터다 — 일반 sync 핸들러는 Flet의
        # 이벤트 루프 위에서 그대로 실행되기 때문에, update() 이후 곧바로 블로킹
        # 호출(네트워크 요청, time.sleep)을 하면 그 update()가 클라이언트로 전송될
        # 틈도 없이 다음 상태로 덮어써져 "검색 중" 화면이 아예 안 뜬 것처럼
        # 보인다. yield 지점에서 Flet이 await asyncio.sleep(0)으로 전송 루프에
        # 한 턴을 양보해주는 게 유일하게 신뢰할 수 있는 중간 갱신 방법이다
        # (flet/controls/base_control.py의 BaseControl._trigger_event 참고).
        extras = [k.strip() for k in extra_field.value.split(",") if k.strip()]
        selected = [kw for kw, sel in keyword_selected.items() if sel]
        all_keywords = list(dict.fromkeys(selected + extras))
        if not all_keywords:
            search_status.value = "키워드를 하나 이상 선택하거나 입력하세요."
            search_status.color = "#B3261E"
            search_status.update()
            return

        # 실제 네트워크 호출이라 몇 초 걸린다 — 버튼 글자를 "검색 중…"으로 바꾸고
        # 비활성화한 뒤, 배너까지 띄워야 "눌렀는데 아무 반응이 없다"는 느낌이 안 든다.
        search_button.content = "⏳ 검색 중…"
        search_button.disabled = True
        search_spinner.visible = True
        search_status.value = ""
        search_status_banner.visible = True
        search_status_banner.bgcolor = "#FFF4E5"
        search_status_banner.content = ft.Row(
            [ft.ProgressRing(width=16, height=16, stroke_width=2), ft.Text(
                "Google News / 네이버 뉴스 검색 중… (몇 초 걸릴 수 있습니다)",
                size=fs(12, scale), color="#8A5A00", weight=ft.FontWeight.BOLD,
            )],
            spacing=10,
        )
        results_box.content = None
        search_button.update()
        search_spinner.update()
        search_status.update()
        search_status_banner.update()
        results_box.update()
        yield  # 여기서 전송 루프에 한 턴을 넘겨 "검색 중" 화면을 실제로 그리게 한다.

        started_at = time.monotonic()
        try:
            results = search_news_by_keywords(all_keywords, int(limit_field.value))
        finally:
            # 캐시가 따뜻하면 검색이 1초도 안 걸려서 "진행 중" 표시가 뜨자마자
            # 사라진다 — 눈으로 인지하기 힘들 만큼 짧으면 최소 시간을 채워준다.
            MIN_VISIBLE_SECONDS = 0.6
            elapsed = time.monotonic() - started_at
            if elapsed < MIN_VISIBLE_SECONDS:
                time.sleep(MIN_VISIBLE_SECONDS - elapsed)
            search_button.content = SEARCH_IDLE_TEXT
            search_button.disabled = False
            search_spinner.visible = False
            search_button.update()
            search_spinner.update()

        # 새 기사가 0건이어도(검색 자체는 됐지만 전부 이미 등록된 경우가 흔하다)
        # 배너를 그냥 숨기면 "검색이 되긴 한 건가?" 싶은 상태가 된다 — 항상 완료를
        # 알리고, 새 기사 유무만 색으로 구분한다.
        if results:
            search_status_banner.bgcolor = "#E7F5EC"
            search_status_banner.content = ft.Text(
                f"✅ 검색 완료 — {len(results)}건", size=fs(12, scale), color="#1B6E3C", weight=ft.FontWeight.BOLD,
            )
        else:
            search_status_banner.bgcolor = "#F0F0F0"
            search_status_banner.content = ft.Text(
                "검색 완료 — 새 기사 없음 (이미 등록된 기사만 있거나 결과가 없습니다)",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"], weight=ft.FontWeight.BOLD,
            )
        search_status_banner.update()
        render_results(results)

    search_button.on_click = on_search

    controls += [
        ft.Text("검색 키워드 (브랜드 킷의 SEO 키워드 — 검색량 큰 순 3개 기본 선택)", size=fs(12, scale)),
        ft.Row(keyword_chips, wrap=True, spacing=6),
        extra_field,
        ft.Text("키워드별 기사 수", size=fs(11, scale), color=BRAND_COLORS["text_muted"]),
        limit_field,
        ft.Row([search_button, search_spinner]),
        search_status_banner,
        search_status,
        results_box,
    ]

    # --- 직접 URL 입력 -----------------------------------------------------------
    url_field = ft.TextField(label="뉴스 URL", hint_text="https://news.example.com/article/123", expand=True)
    manual_memo_field = ft.TextField(
        label="메모 (선택)", hint_text="이 뉴스와 엮고 싶은 자사 맥락",
        multiline=True, min_lines=2, max_lines=4, expand=True,
    )
    url_status = ft.Text("", size=fs(12, scale))

    def on_manual_submit(e: ft.Event) -> None:
        if not url_field.value.strip():
            url_status.value = "뉴스 URL을 입력하세요."
            url_status.color = "#B3261E"
            url_status.update()
            return
        created = repo.insert_campaign(
            source_type="news", source_url=url_field.value.strip(),
            memo=manual_memo_field.value.strip() or None, status="awaiting_media",
        )
        state.pending_workbench_campaign_id = created["id"]
        url_status.value = "✅ 워크벤치로 보냈습니다."
        url_status.color = "#1B6E3C"
        url_status.update()

    controls.append(ft.Divider())
    controls += collapsible(
        "🔗 직접 URL 입력",
        ft.Column(
            [url_field, manual_memo_field, ft.FilledButton("워크벤치로 보내기", on_click=on_manual_submit), url_status],
            spacing=8,
        ),
    )

    # --- 뉴스 기반 콘텐츠 목록 ------------------------------------------------------
    controls.append(ft.Divider())
    controls.append(ft.Text("뉴스 기반 콘텐츠", weight=ft.FontWeight.BOLD, size=fs(16, scale)))
    news_campaigns = repo.list_campaigns(source_type="news")
    if not news_campaigns:
        controls.append(ft.Text("등록된 뉴스 콘텐츠가 없습니다.", size=fs(12, scale), color=BRAND_COLORS["text_muted"]))
    for c in news_campaigns:
        card_controls: list[ft.Control] = [
            ft.Row([
                ft.Text(
                    c.get("title") or c.get("source_title") or c.get("source_url") or "",
                    weight=ft.FontWeight.BOLD, size=fs(13, scale), expand=True,
                ),
                status_chip(c["status"], scale),
            ]),
            ft.Text(
                f"{c.get('created_at', '')} · 사진 {len(c.get('storage_file_paths') or [])}장",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ),
        ]
        if c["status"] == "awaiting_media":
            card_controls.append(ft.Text(
                "📸 워크벤치에서 사진/메모를 붙이고 초안을 생성하세요.",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ))

        # 발행 완료된 글은 워크벤치 드롭다운 자체에서 제외되므로(재편집 대상이
        # 아님) 이동 버튼을 보여줘도 열리지 않는다 — 그 상태만 버튼을 뺀다.
        if c["status"] != "published":
            def on_open_in_workbench(e: ft.Event, campaign_id: str = c["id"]) -> None:
                state.pending_workbench_campaign_id = campaign_id
                if state.navigate_to_workbench:
                    state.navigate_to_workbench()

            card_controls.append(
                ft.TextButton("✍️ 워크벤치에서 열기", icon=ft.Icons.ARROW_FORWARD, on_click=on_open_in_workbench)
            )

        controls.append(ft.Container(
            content=ft.Column(card_controls, spacing=4),
            border=ft.Border.all(1, "#14000000"), border_radius=8, padding=12,
        ))

    return ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
