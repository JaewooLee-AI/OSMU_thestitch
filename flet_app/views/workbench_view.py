"""Flet port of views/02_workbench.py — 편집 패널 + 오른쪽 채널 시뮬레이터.

오른쪽 패널은 `simulators/*.py`가 만드는 HTML을 `st.components.v1.html`
iframe에 그리던 원본과 달리, Flet의 WebView가 Windows를 지원하지 않아
`flet_app/simulators/`(네이티브 컨트롤 트리를 반환하는 별도 패키지)를 쓴다.
어떤 사진이 어디 오는지, 캡션이 어디서 잘리는지 같은 계산 로직은
`simulators.base`/`ai_workers.photo_placement`에서 그대로 재사용하고, 그
결과를 HTML 대신 네이티브로 그리는 부분만 새로 짰다.

Streamlit 버전은 위젯 값이 `session_state`에 남아 있어서, 파이프라인이 DB를
바꿔도 편집창이 옛날 값을 계속 보여주는 문제(`refresh_editor_state`)가 있었다.
Flet은 화면을 다시 그릴 때마다 `repo.get_campaign()`을 새로 읽어 컨트롤을
새로 만들기 때문에 이 문제 자체가 없다 — 별도의 "캐시 무효화" 코드가 필요 없다.
"""
from __future__ import annotations

import time

import flet as ft

from ai_workers import content_mode, factsheet, vision
from ai_workers.content_writer import LENGTH_MODES, revise_content, run_pipeline
from core import repo, storage

import flet_app.simulators as sim
from flet_app.components.collapsible import collapsible
from flet_app.state import AppState
from flet_app.theme import BRAND_COLORS, fs

_STATUS_STYLES = {
    "success": ("#1B6E3C", "#E8F5E9"),
    "warning": ("#8A6D3B", "#FFF6E5"),
    "error": ("#B3261E", "#FDECEA"),
    "info": ("#2F6B7A", "#E7F1F3"),
}


def _status_box(text: str, scale: float, kind: str = "info") -> ft.Container:
    color, bg = _STATUS_STYLES[kind]
    return ft.Container(
        content=ft.Text(text, size=fs(12, scale), color=color),
        bgcolor=bg, padding=10, border_radius=8,
    )


def _label(c: dict) -> str:
    icon = "📰" if c["source_type"] == "news" else "✍️"
    title = c.get("title") or c.get("source_title") or c.get("source_url") or "(제목 없음)"
    return f"{icon} {title[:44]} · {c['status']}"


def _is_untouched_draft(c: dict) -> bool:
    return (
        c["source_type"] == "manual"
        and c["status"] == "awaiting_media"
        and not (c.get("title") or "").strip()
        and not (c.get("memo") or "").strip()
        and not c.get("storage_file_paths")
    )


def _copy_field(label: str, text: str, page: ft.Page, scale: float, help_text: str = "") -> ft.Control:
    # page.set_clipboard()는 이 Flet 버전(1.0)에 없는 메서드다 — 클립보드는
    # 이제 서비스(flet.Clipboard)를 통한 비동기 호출이거나, 여기처럼 클릭
    # 제스처 안에서 클라이언트가 직접 처리하는 action=ft.CopyToClipboard(...)
    # 로 붙인다. page 인자는 다른 호출부와의 시그니처 호환을 위해 남겨둔다.
    return ft.Column(
        [
            ft.Row([
                ft.Text(label, weight=ft.FontWeight.BOLD, size=fs(12, scale)),
                ft.IconButton(
                    icon=ft.Icons.COPY, icon_size=16, tooltip="복사",
                    action=ft.CopyToClipboard(text or ""),
                ),
            ]),
            ft.Container(
                content=ft.Text(text or "(비어 있음)", size=fs(11, scale), selectable=True),
                bgcolor="#F3F1EC", padding=8, border_radius=6,
            ),
            *([ft.Text(help_text, size=fs(10, scale), color=BRAND_COLORS["text_muted"])] if help_text else []),
        ],
        spacing=4,
    )


def _build_sim_panel(campaign_id: str, scale: float, sim_state: dict, refresh_sim) -> ft.Control:
    campaign = repo.get_campaign(campaign_id)
    if not campaign:
        return ft.Text("작업할 콘텐츠를 선택하세요.", size=fs(12, scale), color=BRAND_COLORS["text_muted"])

    brand_kit = repo.get_brand_kit()

    channel_group = ft.RadioGroup(
        value=sim_state["channel"],
        content=ft.Row(
            [ft.Radio(value=k, label=f"{v['icon']} {v['label']}") for k, v in sim.CHANNELS.items()],
            wrap=True,
        ),
    )

    def on_channel_change(e: ft.Event) -> None:
        sim_state["channel"] = channel_group.value
        refresh_sim()

    channel_group.on_change = on_channel_change

    toggles: list[ft.Control] = []
    if sim_state["channel"] == "naver":
        mobile_toggle = ft.Switch(label="모바일 뷰 (390px)", value=sim_state["is_mobile"])

        def on_mobile_change(e: ft.Event) -> None:
            sim_state["is_mobile"] = mobile_toggle.value
            refresh_sim()

        mobile_toggle.on_change = on_mobile_change
        toggles.append(mobile_toggle)
    elif sim_state["channel"] == "shorts":
        dz_toggle = ft.Switch(label="데드존 가이드", value=sim_state["show_dead_zone"])

        def on_dz_change(e: ft.Event) -> None:
            sim_state["show_dead_zone"] = dz_toggle.value
            refresh_sim()

        dz_toggle.on_change = on_dz_change
        toggles.append(dz_toggle)

    preview = sim.render(
        sim_state["channel"], campaign,
        is_mobile=sim_state["is_mobile"], brand_kit=brand_kit, show_dead_zone=sim_state["show_dead_zone"],
    )

    return ft.Column(
        [
            ft.Text("📱 채널 시뮬레이터", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            channel_group,
            *toggles,
            ft.Container(content=preview, padding=ft.Padding.only(top=8)),
        ],
        spacing=8, scroll=ft.ScrollMode.AUTO, expand=True,
    )


def build(page: ft.Page, state: AppState) -> ft.Control:
    scale = state.font_scale
    content_box = ft.Container(expand=True)
    sim_box = ft.Container(expand=True)
    sim_state = {"channel": "naver", "is_mobile": False, "show_dead_zone": True}
    selector = ft.Dropdown(expand=True, label="작업할 콘텐츠")
    empty_notice = ft.Text(
        "작업 중인 콘텐츠가 없습니다. 아래 [➕ 새 콘텐츠]로 시작하세요.",
        size=fs(12, scale), color=BRAND_COLORS["text_muted"],
    )

    def load_editor(campaign_id: str | None, live: bool) -> None:
        if not campaign_id:
            content_box.content = empty_notice
            sim_box.content = ft.Text(
                "작업할 콘텐츠를 선택하세요.", size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            )
        else:
            def reload() -> None:
                load_editor(campaign_id, live=True)

            def refresh_sim() -> None:
                sim_box.content = _build_sim_panel(campaign_id, scale, sim_state, refresh_sim)
                sim_box.update()

            content_box.content = _build_campaign_editor(page, campaign_id, scale, reload, refresh_sim)
            sim_box.content = _build_sim_panel(campaign_id, scale, sim_state, refresh_sim)
        if live:
            content_box.update()
            sim_box.update()

    def refresh_list(preferred_id: str | None = None, live: bool = True) -> None:
        campaigns = repo.list_campaigns()
        editable = [c for c in campaigns if c["status"] != "published"]
        lookup = {c["id"]: c for c in editable}
        options = list(lookup.keys())

        selector.options = [ft.DropdownOption(key=cid, text=_label(lookup[cid])) for cid in options]
        if preferred_id in lookup:
            selector.value = preferred_id
        elif options:
            selector.value = options[0]
        else:
            selector.value = None
        if live:
            selector.update()
        load_editor(selector.value, live=live)

    def on_select(e: ft.Event) -> None:
        load_editor(selector.value, live=True)

    def on_delete(e: ft.Event) -> None:
        if selector.value:
            repo.delete_campaign(selector.value)
            refresh_list()

    def on_new(e: ft.Event) -> None:
        campaigns = repo.list_campaigns()
        editable = [c for c in campaigns if c["status"] != "published"]
        reusable = next((c for c in editable if _is_untouched_draft(c)), None)
        if reusable:
            refresh_list(reusable["id"])
        else:
            created = repo.insert_campaign(source_type="manual", status="awaiting_media")
            refresh_list(created["id"])

    # 이 Flet 버전의 Dropdown에는 on_change가 없다 — 이벤트 이름은 on_select다.
    # (on_change로 지정하면 존재하지 않는 속성이라 조용히 무시되고 콜백이 아예
    # 호출되지 않는다 — 드롭다운에서 골라도 편집 화면이 안 바뀌던 원인.)
    selector.on_select = on_select

    initial_id = state.pending_workbench_campaign_id
    state.pending_workbench_campaign_id = None
    refresh_list(initial_id, live=False)

    return ft.Column(
        [
            ft.Text("워크벤치", size=fs(24, scale), weight=ft.FontWeight.BOLD),
            ft.Row([
                selector,
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, on_click=on_delete, tooltip="삭제"),
                ft.FilledButton("➕ 새 콘텐츠", on_click=on_new),
            ]),
            ft.Divider(),
            ft.Row(
                [content_box, ft.VerticalDivider(width=1), sim_box],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ],
        expand=True,
        spacing=10,
    )


def _build_campaign_editor(page: ft.Page, campaign_id: str, scale: float, reload, refresh_sim) -> ft.Control:
    campaign = repo.get_campaign(campaign_id)
    if not campaign:
        return ft.Text("이 콘텐츠를 찾을 수 없습니다.", color="#B3261E")

    brand_kit = repo.get_brand_kit()
    controls: list[ft.Control] = []

    if campaign.get("source_url"):
        controls.append(ft.Text(f"📰 원문: {campaign['source_url']}", size=fs(11, scale), color=BRAND_COLORS["text_muted"]))
    if campaign.get("source_title"):
        controls.append(ft.Text(
            f"🗞️ 원 기사 제목: {campaign['source_title']} — 참고용입니다. 그대로 쓰면 네이버 유사문서 "
            "판정으로 검색에서 빠집니다.",
            size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))

    title_field = ft.TextField(
        label="제목", value=campaign.get("title") or "",
        hint_text="비워두면 AI가 25자 이내로 지어줍니다", expand=True,
    )
    memo_field = ft.TextField(
        label="담당자 메모 (초안의 씨앗)", value=campaign.get("memo") or "",
        multiline=True, min_lines=5, max_lines=10, expand=True,
        hint_text="예: 성수동 팝업 3일차. 리본핀이 제일 먼저 팔림. 20대 손님이 색감 보고 골랐다가 새활용인 걸 나중에 알고 좋아함.",
    )
    controls += [title_field, memo_field]

    # --- 공지/제품 팩트시트 ---------------------------------------------------
    sheet_fields: dict[str, dict[str, ft.TextField]] = {}
    for sheet, column in ((factsheet.NOTICE, "notice_fields"), (factsheet.PRODUCT, "product_fields")):
        saved = campaign.get(column) or {}
        filled_n = len(factsheet.clean(sheet, saved))
        field_map: dict[str, ft.TextField] = {}
        rows = []
        for i in range(0, len(sheet.fields), 2):
            pair = sheet.fields[i:i + 2]
            row_fields = []
            for fkey, flabel, fplaceholder in pair:
                tf = ft.TextField(label=flabel, value=saved.get(fkey) or "", hint_text=fplaceholder, expand=True)
                field_map[fkey] = tf
                row_fields.append(tf)
            rows.append(ft.Row(row_fields))
        sheet_fields[column] = field_map

        label = sheet.title + (f" ({filled_n}개 입력됨)" if filled_n else "")
        body = ft.Column(
            [ft.Text(sheet.hint + " 채운 항목은 본문에 반드시 들어가고, 비워둔 항목은 AI가 지어내지 않습니다.",
                     size=fs(11, scale), color=BRAND_COLORS["text_muted"])] + rows,
            spacing=8,
        )
        controls += collapsible(label, body, initially_open=bool(filled_n))

    # --- 콘텐츠 모드 -----------------------------------------------------------
    brand_default = content_mode.resolve(None, brand_kit)
    mode_keys = content_mode.ORDER
    current_mode = campaign.get("content_mode")
    mode_group = ft.RadioGroup(
        value=current_mode if current_mode in mode_keys else brand_default["key"],
        content=ft.Row([
            ft.Radio(
                value=k,
                label=f"{content_mode.MODES[k]['icon']} {content_mode.MODES[k]['label']}"
                + (" (기본)" if k == brand_default["key"] else ""),
            )
            for k in mode_keys
        ]),
    )
    mode_caption = ft.Text(
        content_mode.MODES[mode_group.value]["caption"], size=fs(11, scale), color=BRAND_COLORS["text_muted"],
    )

    def on_mode_change(e: ft.Event) -> None:
        mode_caption.value = content_mode.MODES[mode_group.value]["caption"]
        mode_caption.update()

    mode_group.on_change = on_mode_change

    controls += [
        ft.Text("🎚️ 콘텐츠 모드", weight=ft.FontWeight.BOLD, size=fs(13, scale)),
        mode_group,
        mode_caption,
    ]

    # --- 사진 업로드 -----------------------------------------------------------
    attached = list(campaign.get("storage_file_paths") or [])
    photo_row = ft.Row(wrap=True, spacing=8)
    cost_box = ft.Container()
    upload_status = ft.Text("", size=fs(11, scale), color="#B3261E")

    def _render_photos() -> None:
        photo_row.controls = []
        for idx, rel in enumerate(attached):
            def on_remove(e: ft.Event, rel=rel) -> None:
                attached.remove(rel)
                repo.update_campaign(campaign_id, storage_file_paths=attached)
                _render_photos()
                _render_cost()
                photo_row.update()
                cost_box.update()

            photo_row.controls.append(
                ft.Column(
                    [
                        ft.Container(
                            content=ft.Image(src=str(storage.abs_path(rel)), width=90, height=90, fit=ft.BoxFit.COVER),
                            width=90, height=90, border_radius=8, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                            border=ft.Border.all(1, "#E8DFD4"),
                        ),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=16, on_click=on_remove),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2,
                )
            )
        if not attached:
            photo_row.controls.append(ft.Text("첨부된 사진이 없습니다.", size=fs(11, scale), color=BRAND_COLORS["text_muted"]))

    def _render_cost() -> None:
        if not attached:
            cost_box.content = None
            return
        est = vision.preview_cost(attached)
        preset = vision.QUALITY_PRESETS.get(est["quality"], {})
        cost_box.content = ft.Container(
            content=ft.Text(
                f"🪙 이미지 분석 예상 비용 — 사진 {est['images']}장 중 {est['cached']}장은 캐시 적중(비용 0), "
                f"{est['to_analyze']}장만 분석. 예상 입력 토큰 약 {est['est_tokens']:,} "
                f"(화질 {preset.get('label', est['quality'])}, 장당 {est.get('per_image', 0)}토큰)",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ),
            bgcolor="#F3F1EC", padding=8, border_radius=6,
        )

    async def on_pick_photos(e: ft.Event) -> None:
        files = await ft.FilePicker().pick_files(
            allow_multiple=True,
            with_data=True,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["jpg", "jpeg", "png", "webp", "heic"],
        )
        if not files:
            return
        added = False
        for f in files:
            if not f.bytes:
                continue
            rel = storage.save_upload(f.bytes, f.name)
            if rel not in attached:
                attached.append(rel)
                added = True
        if added:
            repo.update_campaign(campaign_id, storage_file_paths=attached)
            upload_status.value = f"{len(files)}장 업로드했습니다."
            _render_photos()
            _render_cost()
            photo_row.update()
            cost_box.update()
            upload_status.update()

    _render_photos()
    _render_cost()

    controls += [
        ft.Row([
            ft.Text("📷 사진", weight=ft.FontWeight.BOLD, size=fs(13, scale)),
            ft.OutlinedButton("사진 추가 (1280px JPEG로 자동 축소 저장)", icon=ft.Icons.UPLOAD_FILE, on_click=on_pick_photos),
        ]),
        photo_row,
        upload_status,
        cost_box,
    ]

    # --- 저장 / 생성 ------------------------------------------------------------
    gen_status = ft.Text("", size=fs(12, scale))
    # run_pipeline 도중 gen_status.value를 계속 갱신해도 이 Flet 버전은
    # page.run_thread 안에서 부른 control.update()를 제때 화면에 반영하지
    # 못한다(다른 창을 클릭해야 밀린 게 한꺼번에 나타나는 것까지 확인됨 —
    # flet-dev/flet#6847과 같은 부류의 버그). ProgressRing의 회전 애니메이션은
    # 일단 화면에 뜨고 나면 Flutter 쪽에서 자체적으로 계속 도는 것이라 이
    # 문제를 아예 타지 않는다 — 그래서 "지금 뭔가 진행 중이다"는 스피너로,
    # 어떤 단계인지는(라이브로는 안 되니) 끝났을 때 한 번에 보여주는 로그로
    # 나눠서 알려준다.
    gen_spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

    def _collect_sheet_values() -> dict:
        out = {}
        for column, field_map in sheet_fields.items():
            sheet = factsheet.NOTICE if column == "notice_fields" else factsheet.PRODUCT
            out[column] = factsheet.clean(sheet, {k: tf.value for k, tf in field_map.items()})
        return out

    def on_save(e: ft.Event) -> None:
        repo.update_campaign(
            campaign_id, title=title_field.value.strip() or None, memo=memo_field.value,
            content_mode=mode_group.value, **_collect_sheet_values(),
        )
        gen_status.value = "💾 저장했습니다."
        gen_status.color = "#1B6E3C"
        gen_status.update()
        refresh_sim()

    can_generate = bool(memo_field.value.strip() or campaign.get("source_url"))
    generate_button = ft.FilledButton("🪄 초안 생성", disabled=not can_generate, expand=True)
    generate_hint = ft.Text(
        "메모를 입력하거나 뉴스 기사를 연결해야 초안을 생성할 수 있습니다.",
        size=fs(11, scale), color=BRAND_COLORS["text_muted"], visible=not can_generate,
    )

    def on_memo_change(e: ft.Event) -> None:
        # generate_button은 이 화면을 처음 열 때의 memo_field 값으로 딱 한 번만
        # disabled가 정해진다 — on_change 없이는 메모를 입력해도 버튼이 계속
        # 비활성 상태로 굳어 있어 클릭할 수 없는 것처럼 보인다.
        now_can_generate = bool(memo_field.value.strip() or campaign.get("source_url"))
        generate_button.disabled = not now_can_generate
        generate_hint.visible = not now_can_generate
        generate_button.update()
        generate_hint.update()

    memo_field.on_change = on_memo_change

    def on_generate(e: ft.Event) -> None:
        repo.update_campaign(
            campaign_id, title=title_field.value.strip() or None, memo=memo_field.value,
            content_mode=mode_group.value, **_collect_sheet_values(),
        )

        # LLM 호출은 몇 초~수십 초가 걸린다 — on_click은 Flet의 이벤트 루프
        # 위에서 그대로 실행되므로, 여기서 직접 블로킹 호출을 하면 그동안 앱
        # 전체가 멈춘 것처럼 보인다(다른 클릭도 안 먹고 진행 표시도 안 뜬다).
        # page.run_thread로 실제 작업을 별도 스레드로 돌려야 이벤트 루프가
        # 자유로워 화면이 계속 반응하고 progress도 실시간으로 반영된다.
        generate_button.disabled = True
        generate_button.update()
        gen_spinner.visible = True
        gen_spinner.update()
        gen_status.value = "⏳ 생성 중입니다… (수십 초 정도 걸릴 수 있어요)"
        gen_status.color = BRAND_COLORS["text_muted"]
        gen_status.update()

        # 단계별 메시지는 여기 담아뒀다가 끝났을 때(성공/실패 어느 쪽이든
        # 한 번은 확실히 반영되는 시점에) 한 번에 보여준다 — 위 스피너 주석
        # 참고.
        progress_log = ["⏳ 시작하는 중…"]

        def on_progress(msg: str) -> None:
            progress_log.append(f"⏳ {msg}")

        def _work() -> None:
            try:
                run_pipeline(campaign_id, progress=on_progress)
            except Exception as exc:  # noqa: BLE001
                # reload()를 부르지 않는다 — 곧바로 부르면 지금 막 띄운 이
                # 실패 메시지가 눈에 보이기도 전에 화면 전체가 새로 그려지며
                # 사라진다(이 화면을 처음 열 때의 빈 상태로). 실패했을 때는
                # 새로 보여줄 콘텐츠도 없으니 다시 그릴 이유도 없다.
                progress_log.append(f"❌ 실패: {exc}")
                gen_status.value = "\n".join(progress_log)
                gen_status.color = "#B3261E"
                gen_spinner.visible = False
                gen_spinner.update()
                generate_button.disabled = not (memo_field.value.strip() or campaign.get("source_url"))
                generate_button.update()
                gen_status.update()
                return
            gen_status.value = "✅ 초안 생성 완료"
            gen_status.color = "#1B6E3C"
            gen_spinner.visible = False
            gen_spinner.update()
            gen_status.update()
            # 성공 경로에서는 버튼을 다시 활성화하지 않는다(비활성 상태 그대로
            # 둔다) — 곧바로 reload()가 이 버튼 자체를 새로 만든 것으로
            # 통째로 갈아 끼우므로, 여기서 활성화했다가 아래 sleep 동안
            # 사용자가 한 번 더 눌러버리면 reload()가 그 두 번째 실행이 쓰던
            # 컨트롤을 화면에서 떼어내며 조용히 죽는 레이스가 생긴다.
            # 성공했을 때는 새로 생성된 콘텐츠 섹션을 보여주기 위해 화면
            # 전체를 다시 그려야 한다 — 그 전에 위 완료 메시지가 잠깐이라도
            # 눈에 보일 시간을 준다.
            time.sleep(0.8)
            reload()

        page.run_thread(_work)

    generate_button.on_click = on_generate
    controls += [
        ft.Row([
            ft.OutlinedButton("💾 저장", on_click=on_save, expand=True),
            generate_button,
        ]),
        ft.Row([gen_spinner, gen_status], spacing=8),
        generate_hint,
    ]

    # --- 생성된 콘텐츠 -----------------------------------------------------------
    if campaign.get("content"):
        controls.append(ft.Divider())
        controls.append(ft.Text("✏️ 생성된 콘텐츠", weight=ft.FontWeight.BOLD, size=fs(14, scale)))
        controls.append(_build_channel_tabs(page, campaign, title_field, scale, reload, refresh_sim))

        controls.append(ft.Divider())

        def on_queue(e: ft.Event) -> None:
            repo.update_campaign(campaign_id, status="ready_to_publish")
            queue_status.value = "🚀 게시 대기열에 넣었습니다. [네이버 게시] 화면에서 진행하세요."
            queue_status.update()

        def on_regenerate(e: ft.Event) -> None:
            # on_generate와 같은 이유로 별도 스레드에서 돌린다 — 그러지 않으면
            # LLM 호출이 끝날 때까지 앱 전체가 멈춘 것처럼 보인다.
            regenerate_button.disabled = True
            regenerate_button.update()
            queue_spinner.visible = True
            queue_spinner.update()
            queue_status.value = "⏳ 생성 중입니다… (수십 초 정도 걸릴 수 있어요)"
            queue_status.update()

            progress_log = ["⏳ 시작하는 중…"]

            def on_progress(msg: str) -> None:
                progress_log.append(f"⏳ {msg}")

            def _work() -> None:
                try:
                    run_pipeline(campaign_id, progress=on_progress)
                except Exception as exc:  # noqa: BLE001
                    # 바로 reload()하면 이 실패 메시지가 보이기도 전에 화면이
                    # 다시 그려지며 사라진다 — on_generate와 같은 이유.
                    progress_log.append(f"❌ 실패: {exc}")
                    queue_status.value = "\n".join(progress_log)
                    queue_status.color = "#B3261E"
                    queue_spinner.visible = False
                    queue_spinner.update()
                    regenerate_button.disabled = False
                    regenerate_button.update()
                    queue_status.update()
                    return
                queue_status.value = "✅ 완료"
                queue_status.color = "#1B6E3C"
                queue_spinner.visible = False
                queue_spinner.update()
                queue_status.update()
                # on_generate와 같은 이유로 성공 경로에서는 버튼을 다시
                # 활성화하지 않는다 — 곧 reload()가 이 버튼을 통째로 새로
                # 만든 것으로 갈아 끼운다.
                time.sleep(0.8)
                reload()

            page.run_thread(_work)

        queue_status = ft.Text("", size=fs(12, scale), color="#1B6E3C")
        queue_spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
        regenerate_button = ft.OutlinedButton("🔁 초안 다시 생성", on_click=on_regenerate, expand=True)
        controls += [
            ft.Row([
                ft.FilledButton("🚀 네이버 게시 대기열로", on_click=on_queue, expand=True),
                regenerate_button,
            ]),
            ft.Row([queue_spinner, queue_status], spacing=8),
        ]

        controls += _build_report_controls(campaign, scale)

    return ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)


def _build_channel_tabs(page: ft.Page, campaign: dict, title_field: ft.TextField, scale: float, reload, refresh_sim) -> ft.Control:
    campaign_id = campaign["id"]

    # --- 네이버 본문 --------------------------------------------------------
    body_field = ft.TextField(
        label="본문 ([IMAGE: 경로] 위치를 옮기면 사진 배치가 바뀝니다)",
        value=campaign.get("content") or "", multiline=True, min_lines=14, max_lines=24, expand=True,
    )
    naver_tags_field = ft.TextField(label="발행 태그", value=" ".join(campaign.get("naver_hashtags") or []), expand=True)
    naver_status = ft.Text("", size=fs(12, scale), color="#1B6E3C")

    def on_save_naver(e: ft.Event) -> None:
        repo.update_campaign(
            campaign_id, title=title_field.value.strip() or None, content=body_field.value,
            naver_hashtags=[t for t in naver_tags_field.value.split() if t.strip()],
        )
        naver_status.value = "저장했습니다."
        naver_status.update()
        refresh_sim()

    revise_field = ft.TextField(
        label="수정 요청", hint_text="예: 도입부를 더 친근한 말투로 바꾸고, 가격 이야기는 빼주세요.",
        multiline=True, min_lines=2, max_lines=4, expand=True,
    )
    length_group = ft.RadioGroup(
        value="keep",
        content=ft.Row([ft.Radio(value=k, label=v["label"]) for k, v in LENGTH_MODES.items()]),
    )
    revise_status = ft.Text("", size=fs(12, scale))
    revise_spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
    revise_button = ft.FilledButton("✏️ 수정 반영")

    def on_revise(e: ft.Event) -> None:
        repo.update_campaign(campaign_id, content=body_field.value)

        # on_generate와 같은 이유로 별도 스레드에서 돌린다.
        revise_button.disabled = True
        revise_button.update()
        revise_spinner.visible = True
        revise_spinner.update()
        revise_status.value = "⏳ 수정하고 있습니다…"
        revise_status.color = BRAND_COLORS["text_muted"]
        revise_status.update()

        progress_log = ["⏳ 시작하는 중…"]

        def on_progress(msg: str) -> None:
            progress_log.append(f"⏳ {msg}")

        def _work() -> None:
            try:
                revise_content(campaign_id, instruction=revise_field.value, length_mode=length_group.value, progress=on_progress)
            except Exception as exc:  # noqa: BLE001
                # 바로 reload()하면 이 실패 메시지가 보이기도 전에 화면이
                # 다시 그려지며 사라진다 — on_generate와 같은 이유.
                progress_log.append(f"❌ 실패: {exc}")
                revise_status.value = "\n".join(progress_log)
                revise_status.color = "#B3261E"
                revise_spinner.visible = False
                revise_spinner.update()
                revise_button.disabled = False
                revise_button.update()
                revise_status.update()
                return
            revise_status.value = "✅ 수정 완료"
            revise_status.color = "#1B6E3C"
            revise_spinner.visible = False
            revise_spinner.update()
            revise_status.update()
            # on_generate와 같은 이유로 성공 경로에서는 버튼을 다시
            # 활성화하지 않는다 — 곧 reload()가 이 버튼을 통째로 새로
            # 만든 것으로 갈아 끼운다.
            time.sleep(0.8)
            reload()

        page.run_thread(_work)

    revise_button.on_click = on_revise

    naver_tab = ft.Column(
        [
            ft.Text(f"제목 · {len(title_field.value)}자", size=fs(11, scale), color=BRAND_COLORS["text_muted"]),
            ft.Text(title_field.value or "(제목 없음)", size=fs(18, scale), weight=ft.FontWeight.BOLD),
            body_field,
            naver_tags_field,
            ft.Row([ft.FilledButton("본문 저장", on_click=on_save_naver), naver_status]),
            *collapsible("📋 복사해서 네이버에 직접 붙여넣기", ft.Column([
                _copy_field("제목", title_field.value, page, scale),
                _copy_field("본문", body_field.value, page, scale, "[IMAGE: …] 자리에 해당 순서의 사진을 넣으면 됩니다"),
                _copy_field("발행 태그", naver_tags_field.value, page, scale),
            ], spacing=8)),
            ft.Divider(),
            ft.Text("✏️ 수정 요청해서 다시 쓰기", weight=ft.FontWeight.BOLD, size=fs(13, scale)),
            ft.Text(
                f"현재 {len(body_field.value)}자. 편집창 내용을 그대로 이어받아 고쳐 씁니다.",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ),
            revise_field,
            length_group,
            revise_button,
            ft.Text("요청 없이 눌러도 맞춤법·오탈자 교정은 항상 실행됩니다.", size=fs(10, scale), color=BRAND_COLORS["text_muted"]),
            ft.Row([revise_spinner, revise_status], spacing=8),
        ],
        spacing=8, scroll=ft.ScrollMode.AUTO,
    )

    # --- 인스타그램 --------------------------------------------------------
    ig_caption_field = ft.TextField(
        label="캡션 (첫 125자가 '더보기' 앞에 노출됩니다)",
        value=campaign.get("instagram_caption") or "", multiline=True, min_lines=8, max_lines=14, expand=True,
    )
    ig_tags_field = ft.TextField(label="해시태그", value=" ".join(campaign.get("instagram_hashtags") or []), expand=True)
    ig_status = ft.Text("", size=fs(12, scale), color="#1B6E3C")

    def on_save_ig(e: ft.Event) -> None:
        repo.update_campaign(
            campaign_id, instagram_caption=ig_caption_field.value,
            instagram_hashtags=[t for t in ig_tags_field.value.split() if t.strip()],
        )
        ig_status.value = "저장했습니다."
        ig_status.update()
        refresh_sim()

    ig_tab = ft.Column(
        [
            ig_caption_field,
            ft.Text(f"현재 {len(ig_caption_field.value)}자 — 훅은 앞 125자 안에 들어가야 합니다.", size=fs(11, scale), color=BRAND_COLORS["text_muted"]),
            ig_tags_field,
            ft.Row([ft.FilledButton("캡션 저장", on_click=on_save_ig), ig_status]),
            *collapsible("📋 복사해서 인스타그램에 직접 붙여넣기", ft.Column([
                _copy_field("캡션", ig_caption_field.value, page, scale),
                _copy_field("해시태그", ig_tags_field.value, page, scale),
                _copy_field("캡션 + 해시태그 (한 번에)", f"{ig_caption_field.value}\n\n{ig_tags_field.value}".strip(), page, scale),
            ], spacing=8)),
        ],
        spacing=8, scroll=ft.ScrollMode.AUTO,
    )

    # --- X 스레드 --------------------------------------------------------
    tweets = campaign.get("x_content") or []
    x_body_field = ft.TextField(
        label="트윗 (빈 줄로 구분하면 각각 하나의 트윗이 됩니다)",
        value="\n\n".join(tweets), multiline=True, min_lines=8, max_lines=14, expand=True,
    )
    x_tags_field = ft.TextField(label="해시태그 (1~2개 권장)", value=" ".join(campaign.get("x_hashtags") or []), expand=True)
    x_status = ft.Text("", size=fs(12, scale), color="#1B6E3C")

    def on_save_x(e: ft.Event) -> None:
        repo.update_campaign(
            campaign_id,
            x_content=[t.strip() for t in x_body_field.value.split("\n\n") if t.strip()],
            x_hashtags=[t for t in x_tags_field.value.split() if t.strip()],
        )
        x_status.value = "저장했습니다."
        x_status.update()
        refresh_sim()

    current_tweets = [t.strip() for t in x_body_field.value.split("\n\n") if t.strip()]
    x_tab = ft.Column(
        [
            x_body_field,
            x_tags_field,
            ft.Row([ft.FilledButton("스레드 저장", on_click=on_save_x), x_status]),
            *collapsible("📋 복사해서 X에 직접 붙여넣기", ft.Column(
                [_copy_field(f"트윗 {i}/{len(current_tweets)}", t, page, scale, f"{len(t)}자") for i, t in enumerate(current_tweets, start=1)]
                + [_copy_field("해시태그", x_tags_field.value, page, scale)],
                spacing=8,
            )),
        ],
        spacing=8, scroll=ft.ScrollMode.AUTO,
    )

    # --- 쇼츠 --------------------------------------------------------
    script = campaign.get("shorts_script") or {}
    if script.get("scenes"):
        scene_controls: list[ft.Control] = [ft.Text(script.get("title", ""), weight=ft.FontWeight.BOLD, size=fs(13, scale))]
        for i, scene in enumerate(script["scenes"], start=1):
            scene_controls.append(ft.Text(f"CUT {i} — {scene.get('caption', '')}", size=fs(12, scale)))
            scene_controls.append(ft.Text(scene.get("shot", ""), size=fs(10, scale), color=BRAND_COLORS["text_muted"]))
        copy_children = [_copy_field("제목", script.get("title", ""), page, scale)]
        if script.get("hook"):
            copy_children.append(_copy_field("훅 (첫 3초)", script["hook"], page, scale))
        copy_children.append(_copy_field(
            "자막 전체",
            "\n".join(f"CUT {i}. {s.get('caption', '')}" for i, s in enumerate(script["scenes"], start=1)),
            page, scale,
        ))
        if script.get("hashtags"):
            copy_children.append(_copy_field("해시태그", " ".join(script["hashtags"]), page, scale))
        shorts_tab = ft.Column(
            scene_controls + collapsible("📋 복사해서 영상 편집기에 붙여넣기", ft.Column(copy_children, spacing=8)),
            spacing=8, scroll=ft.ScrollMode.AUTO,
        )
    else:
        shorts_tab = ft.Text("쇼츠 구성안이 아직 없습니다.", size=fs(12, scale), color=BRAND_COLORS["text_muted"])

    return ft.Tabs(
        length=4,
        height=560,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(tabs=[
                    ft.Tab(label="네이버 본문"), ft.Tab(label="인스타 캡션"),
                    ft.Tab(label="X 스레드"), ft.Tab(label="쇼츠 자막"),
                ]),
                ft.TabBarView(expand=True, controls=[
                    ft.Container(content=naver_tab, padding=10),
                    ft.Container(content=ig_tab, padding=10),
                    ft.Container(content=x_tab, padding=10),
                    ft.Container(content=shorts_tab, padding=10),
                ]),
            ],
        ),
    )


def _build_report_controls(campaign: dict, scale: float) -> list[ft.Control]:
    report = campaign.get("guardrail_report")
    controls: list[ft.Control] = []

    for key, sheet in (("notice", factsheet.NOTICE), ("product", factsheet.PRODUCT)):
        section = (report or {}).get(key) or {}
        if section.get("checked") and section.get("missing_labels"):
            controls.append(_status_box(
                f"{sheet.title} 누락 — {', '.join(section['missing_labels'])} 이(가) 본문에 반영되지 않았습니다. "
                "본문에서 직접 넣고 저장하세요.",
                scale, "warning",
            ))

    sns_labels = {"instagram": "📸 인스타그램 캡션", "x": "🐦 X 스레드", "shorts": "🎬 쇼츠 자막"}
    for ch_key, ch_label in sns_labels.items():
        compliance = ((report or {}).get("sns_compliance") or {}).get(ch_key) or {}
        if compliance.get("checked") and compliance.get("compliance_pass") is False:
            controls.append(_status_box(
                f"{ch_label} 컴플라이언스 미해결 — " + " / ".join(compliance.get("issues") or []), scale, "warning",
            ))

    if not report:
        return controls

    body: list[ft.Control] = []
    if report.get("compliance_pass") is None:
        body.append(ft.Text("가드레일이 꺼진 상태로 생성되어 검수를 건너뛰었습니다.", size=fs(12, scale)))
    else:
        icon = "✅" if report.get("compliance_pass") else "⚠️"
        body.append(ft.Text(f"{icon} 컴플라이언스 검수 점수: {report.get('score', '-')}/100", weight=ft.FontWeight.BOLD, size=fs(13, scale)))
        for strength in report.get("strengths") or []:
            body.append(ft.Text(f"👍 {strength}", size=fs(11, scale)))
        for hit in report.get("dictionary_hits") or []:
            body.append(ft.Text(f"🚫 자동 치환(이미 반영됨): {hit['forbidden']} → {hit['replacement']}", size=fs(11, scale)))
        for issue in report.get("llm_issues") or []:
            body.append(ft.Text(f"🤖 {issue}", size=fs(11, scale)))
        for issue in report.get("resolved_issues") or []:
            body.append(ft.Text(f"✅ (교정되어 이미 해결됨) {issue}", size=fs(11, scale)))
        for issue in report.get("unverified_issues") or []:
            body.append(ft.Text(f"❓ (검증 안 됨, 참고용) {issue}", size=fs(11, scale)))
        for tip in report.get("suggestions") or []:
            body.append(ft.Text(f"💡 (위반 아님 · 보완 제안) {tip}", size=fs(11, scale)))

    used_mode = report.get("content_mode")
    if used_mode:
        body.append(ft.Text(f"🎚️ 생성 모드: {content_mode.label_of(used_mode)}", size=fs(11, scale)))
    for hit in report.get("title_dictionary_hits") or []:
        body.append(ft.Text(f"🛡️ 제목 금기어 치환: '{hit['forbidden']}' → '{hit['replacement']}'", size=fs(11, scale)))

    density = report.get("seo_density")
    if density and density.get("enforced") is False:
        counts = density.get("counts") or {}
        body.append(ft.Text(
            "📖 내용 우선 모드 — 키워드 밀도를 강제하지 않았습니다. "
            + (f"자연스럽게 등장한 횟수: {counts}" if counts else "이 글에는 브랜드 키워드가 등장하지 않았습니다."),
            size=fs(11, scale),
        ))
    elif report.get("seo_targets") == [] and (report.get("seo_pool") or []) and used_mode != "rich":
        body.append(ft.Text(
            "🎯 SEO 타깃 없음 — 이 글의 소재에 맞는 키워드가 브랜드 킷 목록에 없습니다. "
            "이 주제로 검색 노출을 노리려면 🧵 브랜드 킷에 해당 분야 키워드를 추가하세요.",
            size=fs(11, scale),
        ))
    if density and density.get("status") != "no_keywords" and density.get("enforced") is not False:
        icon = {"optimal": "✅", "low": "📉", "high": "📈"}.get(density["status"], "•")
        targets = report.get("seo_targets") or []
        label = f" — 이번 글 타깃: {', '.join(targets)}" if targets else ""
        body.append(ft.Text(f"{icon} SEO 키워드 밀도: {density['status']}{label}", size=fs(11, scale)))
        counts = density.get("counts") or {}
        if counts:
            body.append(ft.Text("  " + ", ".join(f"{k}: {v}" for k, v in counts.items()), size=fs(10, scale), color=BRAND_COLORS["text_muted"]))
        skipped = report.get("seo_skipped") or []
        if skipped:
            body.append(ft.Text("· 이번 글 주제와 맞지 않아 제외한 키워드: " + ", ".join(skipped), size=fs(10, scale), color=BRAND_COLORS["text_muted"]))

    intent = report.get("search_intent")
    if intent and intent.get("checked"):
        coverage, missing = intent.get("coverage"), intent.get("missing") or []
        icon = "✅" if not missing else ("⚠️" if (coverage or 0) >= 50 else "🔴")
        body.append(ft.Text(f"{icon} 검색 의도 충족도: {coverage}% ('{intent.get('keyword')}'로 검색한 사람 기준)", size=fs(11, scale)))
        if missing:
            body.append(ft.Text("· 아래 정보를 본문에 직접 채우면 노출 순위가 올라갑니다:", size=fs(10, scale)))
            for item in missing:
                body.append(ft.Text(f"　🔸 {item.get('item')} — {item.get('why', '')}", size=fs(10, scale)))

    regressed = report.get("seo_regression_fixed")
    if regressed:
        body.append(ft.Text("🔒 SEO 보정이 되살린 컴플라이언스 문구를 다시 교정했습니다: " + ", ".join(regressed), size=fs(11, scale)))

    title_seo = report.get("title_seo")
    if title_seo and title_seo.get("checked"):
        body.append(ft.Text(
            "✏️ 제목에 타깃 키워드가 없어 자동 보강했습니다." if title_seo.get("fixed") else "✅ 제목에 타깃 키워드가 이미 포함되어 있습니다.",
            size=fs(11, scale),
        ))

    sns = report.get("sns_checks")
    if sns:
        all_issues = (sns.get("x") or []) + (sns.get("instagram") or [])
        if all_issues:
            for issue in all_issues:
                icon = {"fixed": "🔧", "warn": "⚠️", "blocked": "⛔"}.get(issue["level"], "•")
                body.append(ft.Text(f"{icon} SNS 형식: {issue['message']}", size=fs(11, scale)))
        else:
            body.append(ft.Text("✅ SNS 형식(트윗 길이·해시태그·훅): 문제 없음", size=fs(11, scale)))

    pf = report.get("proofread")
    if pf is not None:
        applied, rejected = pf.get("applied") or [], pf.get("rejected") or []
        if applied:
            body.append(ft.Text(f"🔤 맞춤법·오탈자 {len(applied)}건 교정", size=fs(11, scale)))
            for fix in applied:
                times = f" ×{fix['count']}" if fix.get("count", 1) > 1 else ""
                body.append(ft.Text(f"　• [{fix.get('kind', '교정')}] {fix['before']} → {fix['after']}{times}", size=fs(10, scale)))
        else:
            body.append(ft.Text("🔤 맞춤법·오탈자: 발견된 오류 없음", size=fs(11, scale)))
        for bad in rejected:
            body.append(ft.Text(f"　• ⛔ 교정 보류 ({bad['reason']}): {bad['before']} → {bad['after']}", size=fs(10, scale)))

    revision = report.get("revision")
    if revision:
        before, after = revision["before_chars"], revision["after_chars"]
        delta = f"{(after - before) / before:+.0%}" if before else "-"
        mode_label = LENGTH_MODES.get(revision["length_mode"], {}).get("label", "")
        body.append(ft.Text(f"✏️ 수정 반영: {before:,}자 → {after:,}자 ({delta}, 요청 '{mode_label}')", size=fs(11, scale)))

    variety = report.get("title_variety")
    if variety and variety.get("checked"):
        if variety.get("rewritten"):
            body.append(ft.Text(
                f"🎲 과거 제목과 유사도 {variety.get('score', 0):.0%} — 다른 제목으로 다시 지었습니다.", size=fs(11, scale),
            ))
        else:
            body.append(ft.Text(f"✅ 과거 제목과 충분히 다릅니다 (최대 유사도 {variety.get('score', 0):.0%}).", size=fs(11, scale)))

    recommendation = report.get("recommendation")
    if recommendation:
        body.append(ft.Divider())
        verdict = recommendation["verdict"]
        if verdict == "ok":
            body.append(_status_box("✅ 종합 판정: 그대로 게시해도 좋습니다.", scale, "success"))
        elif verdict == "regenerate":
            body.append(_status_box("🔁 종합 판정: 다시 생성을 고려하세요 — " + " / ".join(recommendation["reasons"]), scale, "warning"))
        else:
            body.append(_status_box("📝 종합 판정: 본문 자체는 게시해도 좋습니다. 다만 아래를 채우면 검색 노출이 달라집니다.", scale, "info"))

        if recommendation.get("intent_gap"):
            body.append(ft.Text(
                f"🔴 검색 의도 충족도 {recommendation.get('intent_coverage')}% — 아래 항목을 🛍️ 제품·주문 정보에 채우고 다시 생성하세요:",
                size=fs(11, scale),
            ))
            for item in recommendation.get("intent_missing") or []:
                body.append(ft.Text(f"　🔸 {item if isinstance(item, str) else str(item)}", size=fs(10, scale)))

        for gap in recommendation.get("fact_gaps") or []:
            body.append(ft.Text(f"📋 입력했지만 본문에 안 들어간 항목 — {gap}", size=fs(11, scale)))

        if recommendation.get("no_targets"):
            body.append(ft.Text(
                "🎯 이 글에 맞는 SEO 키워드가 브랜드 킷에 없어 타깃 없이 발행됩니다. 키워드를 추가해야 해결됩니다.",
                size=fs(11, scale),
            ))

        conflicts = recommendation.get("conflicts") or []
        if conflicts:
            body.append(ft.Text(
                f"⚖️ 타깃 키워드 {', '.join(conflicts)}는 컴플라이언스 검수에서 제거되는 표현이라 다시 생성해도 채워지지 않습니다.",
                size=fs(11, scale),
            ))

    controls += collapsible("🛡️ 컴플라이언스 / SEO 리포트", ft.Column(body, spacing=6))
    return controls
