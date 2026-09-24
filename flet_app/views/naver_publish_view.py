"""네이버 블로그 반자동 게시.

네이버가 봇의 Smart Editor ONE 게시를 적극적으로 막기 때문에 이 화면은
"반"자동이다 — 저장된 로그인 세션으로 실제 Chrome 창을 열어 제목·본문·사진을
순서대로 붙여넣어주고, 담당자가 직접 [발행]을 눌러야 한다. `발행` 팝업의 태그
칸은 자동화가 아예 닿지 않아, 여기서 태그를 보여주고 복사만 시킨다.

`open_naver_login_session()`과 게시 둘 다 몇 분 이상 블로킹되는 호출이라
워크벤치의 초안 생성과 같은 이유로 `page.run_thread()`로 돌린다 — 그러지
않으면 그 창이 열려 있는 동안 앱 전체가 멈춘 것처럼 보인다. 게시는
`naver_paste_worker.run()`을 같은 프로세스 안 스레드에서 직접 부른다(패키징된
Flet exe에는 subprocess로 띄울 python.exe가 없다). run()은 담당자가 게시 창을
닫을 때까지 돌아오지 않고, 같은 글을 두 번 게시하려는 클릭은 run() 쪽에서
거른다.
"""
from __future__ import annotations

import time

import flet as ft

import flet_app.simulators as sim
from ai_workers import naver_paste_worker
from ai_workers.naver_publisher import (
    clear_naver_session,
    naver_session_exists,
    open_naver_login_session,
    split_publish_error,
)
from ai_workers.sns_validator import TWEET_HARD_MAX, x_weighted_length
from core import repo

from flet_app.components.collapsible import collapsible
from flet_app.state import AppState
from flet_app.theme import BRAND_COLORS, fs
from flet_app.views.workbench_view import _copy_field


def _publish_error_box(campaign: dict, scale: float) -> list[ft.Control]:
    summary, detail = split_publish_error(campaign.get("publish_error") or "")
    if not summary:
        return []
    out: list[ft.Control] = [
        ft.Container(
            content=ft.Text(summary, size=fs(12, scale), color="#B3261E"),
            bgcolor="#FDECEA", padding=10, border_radius=8,
        )
    ]
    if detail:
        out += collapsible(
            "자세한 오류 내용",
            ft.Text(detail, size=fs(11, scale), selectable=True, font_family="monospace"),
        )
    return out


def _fields_for_channel(page: ft.Page, scale: float, campaign: dict, channel: str) -> ft.Control:
    if channel == "naver":
        return ft.Column([
            _copy_field("제목", campaign.get("title") or "", page, scale),
            _copy_field("본문", campaign.get("content") or "", page, scale),
        ], spacing=8)
    if channel == "instagram":
        controls: list[ft.Control] = [
            _copy_field("캡션", campaign.get("instagram_caption") or "", page, scale),
        ]
        tags = campaign.get("instagram_hashtags") or []
        if tags:
            controls.append(_copy_field("해시태그", " ".join(tags), page, scale))
        return ft.Column(controls, spacing=8)
    if channel == "x":
        controls = []
        tweets = campaign.get("x_content") or []
        for i, tweet in enumerate(tweets, start=1):
            controls.append(_copy_field(f"트윗 {i}/{len(tweets)}", tweet, page, scale, f"X 기준 {x_weighted_length(tweet)}/{TWEET_HARD_MAX}"))
        tags = campaign.get("x_hashtags") or []
        if tags:
            controls.append(_copy_field("해시태그", " ".join(tags), page, scale))
        return ft.Column(controls, spacing=8)
    if channel == "shorts":
        script = campaign.get("shorts_script") or {}
        controls = []
        if script.get("title"):
            controls.append(_copy_field("제목", script["title"], page, scale))
        if script.get("hook"):
            controls.append(_copy_field("훅 (첫 3초)", script["hook"], page, scale))
        if script.get("scenes"):
            text = "\n".join(
                f"CUT {i}. {s.get('caption', '')}" for i, s in enumerate(script["scenes"], start=1)
            )
            controls.append(_copy_field("자막 전체", text, page, scale))
        if script.get("hashtags"):
            controls.append(_copy_field("해시태그", " ".join(script["hashtags"]), page, scale))
        return ft.Column(controls, spacing=8)
    return ft.Column()


def _build_content_preview(page: ft.Page, scale: float, brand_kit: dict, campaign: dict) -> list[ft.Control]:
    initial_channel = "naver"
    preview_box = ft.Container(content=sim.render(initial_channel, campaign, brand_kit=brand_kit))
    fields_box = ft.Container(content=_fields_for_channel(page, scale, campaign, initial_channel))

    channel_group = ft.RadioGroup(
        value=initial_channel,
        content=ft.Row(
            [ft.Radio(value=k, label=f"{v['icon']} {v['label']}") for k, v in sim.CHANNELS.items()],
            wrap=True,
        ),
    )

    def on_channel_change(e: ft.Event) -> None:
        channel = channel_group.value
        preview_box.content = sim.render(channel, campaign, brand_kit=brand_kit)
        fields_box.content = _fields_for_channel(page, scale, campaign, channel)
        preview_box.update()
        fields_box.update()

    channel_group.on_change = on_channel_change

    return collapsible(
        "📄 콘텐츠 보기 (전체 채널)",
        ft.Column([
            channel_group,
            ft.Container(content=preview_box, padding=ft.Padding.only(top=8)),
            ft.Divider(),
            fields_box,
        ], spacing=8),
    )


def _build_card(
    page: ft.Page, scale: float, brand_kit: dict, campaign: dict,
    key_prefix: str, publish_label: str, allow_manual_complete: bool, refresh_all,
) -> ft.Control:
    campaign_id = campaign["id"]
    confirm_delete = {"value": False}
    action_box = ft.Container()
    publish_status = ft.Text("", size=fs(12, scale))

    def build_actions() -> ft.Control:
        if confirm_delete["value"]:
            def on_confirm(e: ft.Event) -> None:
                repo.delete_campaign(campaign_id)
                refresh_all()

            def on_cancel(e: ft.Event) -> None:
                confirm_delete["value"] = False
                action_box.content = build_actions()
                action_box.update()

            return ft.Row([
                ft.FilledButton("🗑️ 삭제 확인", on_click=on_confirm, expand=True),
                ft.OutlinedButton("취소", on_click=on_cancel, expand=True),
                ft.Text("삭제하면 되돌릴 수 없습니다.", size=fs(11, scale), color=BRAND_COLORS["text_muted"]),
            ], spacing=8)

        def on_publish(e: ft.Event) -> None:
            if not naver_session_exists():
                publish_status.value = "네이버 로그인 세션이 없습니다. 먼저 로그인 세션을 저장하세요."
                publish_status.color = "#B3261E"
                publish_status.update()
                return
            if naver_paste_worker.is_running(campaign_id):
                return  # 두 번째 클릭 — 같은 글이 이미 게시 창에 붙여넣어지는 중

            publish_button.disabled = True
            publish_button.update()
            publish_status.value = "게시 작업을 시작했습니다. 잠시 후 Chrome 창이 열리면 내용을 확인하고 [발행] 버튼을 직접 눌러주세요."
            publish_status.color = "#1B6E3C"
            publish_status.update()

            def on_status(msg: str) -> None:
                publish_status.value = msg
                publish_status.color = "#B3261E" if msg.startswith(("❌", "⚠️")) else "#1B6E3C"
                try:
                    publish_status.update()
                except Exception:  # noqa: BLE001 — 화면이 이미 다시 그려졌으면 무시
                    pass

            def _work() -> None:
                # run()이 게시 창이 닫힐 때까지 돌아오지 않으므로, 그동안 버튼은
                # 꺼진 채로 둔다. 예외는 run() 안에서 publish_error로 기록되지만
                # 그 밖에서 터져도 버튼이 영원히 꺼져 있지 않도록 finally로 복구.
                try:
                    naver_paste_worker.run(campaign_id, on_status=on_status)
                except Exception as exc:  # noqa: BLE001
                    on_status(f"❌ 게시 실패: {exc}")
                finally:
                    publish_button.disabled = False
                    try:
                        publish_button.update()
                    except Exception:  # noqa: BLE001
                        pass

            page.run_thread(_work)

        running = naver_paste_worker.is_running(campaign_id)
        if running:
            publish_status.value = "⏳ 이 글의 게시 창이 열려 있습니다. Chrome 창에서 확인 후 [발행]을 눌러주세요."
            publish_status.color = "#1B6E3C"
        publish_button = ft.FilledButton(
            publish_label, on_click=on_publish, expand=True,
            disabled=running or not naver_session_exists(),
            tooltip=None if naver_session_exists() else "먼저 네이버에 로그인해주세요.",
        )
        buttons: list[ft.Control] = [publish_button]
        if allow_manual_complete:
            def on_manual(e: ft.Event) -> None:
                repo.update_campaign(campaign_id, status="published", publish_error=None)
                refresh_all()

            buttons.append(ft.OutlinedButton("✅ 수동으로 완료", on_click=on_manual, expand=True))

        def on_ask_delete(e: ft.Event) -> None:
            confirm_delete["value"] = True
            action_box.content = build_actions()
            action_box.update()

        buttons.append(ft.OutlinedButton("🗑️ 삭제", on_click=on_ask_delete, expand=True))

        return ft.Column([ft.Row(buttons, spacing=8), publish_status], spacing=6)

    action_box.content = build_actions()

    icon = "📰" if campaign["source_type"] == "news" else "✍️"
    card_children: list[ft.Control] = [
        ft.Text(f"{icon} {campaign.get('title') or '(제목 없음)'}", size=fs(15, scale), weight=ft.FontWeight.BOLD),
    ]
    if key_prefix == "pending":
        photo_count = len(campaign.get("storage_file_paths") or [])
        card_children.append(ft.Text(
            f"업데이트: {campaign.get('updated_at', '')} · 사진 {photo_count}장",
            size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))
    else:
        card_children.append(ft.Text(
            f"게시: {campaign.get('updated_at', '')}", size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))
    if campaign.get("source_url"):
        card_children.append(ft.Text(
            f"📰 원문: {campaign['source_url']}", size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))

    card_children += _publish_error_box(campaign, scale)
    card_children += _build_content_preview(page, scale, brand_kit, campaign)

    hashtags = campaign.get("naver_hashtags") or []
    if hashtags:
        hint = (
            "🏷️ 태그는 자동으로 입력되지 않습니다. 아래를 복사해 [발행] 창의 태그 칸에 붙여넣어 주세요."
            if key_prefix == "pending" else "🏷️ [발행] 창 태그 칸에 붙여넣을 해시태그"
        )
        card_children += [
            ft.Text(hint, size=fs(11, scale), color=BRAND_COLORS["text_muted"]),
            ft.Container(
                content=ft.Text(" ".join(hashtags), size=fs(12, scale), selectable=True),
                bgcolor="#F3F1EC", padding=8, border_radius=6,
            ),
        ]

    if key_prefix == "pending":
        card_children.append(ft.Text(
            "직접 로그인 없이 [📄 콘텐츠 보기]에서 복사해 네이버에 손으로 붙여넣었다면, "
            "아래 [✅ 수동으로 완료]를 눌러 이 목록에서 정리하세요.",
            size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))

    card_children.append(action_box)

    return ft.Container(
        content=ft.Column(card_children, spacing=8),
        border=ft.Border.all(1, "#E4DCC8"), border_radius=10, padding=14,
    )


def _build_login_section(page: ft.Page, scale: float, naver_blog_id: str, refresh_all) -> ft.Control:
    if naver_session_exists():
        def on_logout(e: ft.Event) -> None:
            clear_naver_session()
            refresh_all()

        return ft.Row([
            ft.Container(
                content=ft.Text(
                    f"🔑 네이버 로그인됨 (blog.naver.com/{naver_blog_id})",
                    size=fs(12, scale), color="#1B6E3C",
                ),
                bgcolor="#E8F5E9", padding=10, border_radius=8, expand=True,
            ),
            ft.OutlinedButton("🧹 로그아웃", on_click=on_logout),
        ])

    login_status = ft.Text("", size=fs(12, scale))
    login_button = ft.FilledButton("🔓 네이버 로그인", disabled=not naver_blog_id)

    def on_login(e: ft.Event) -> None:
        # 최대 2분 블로킹되는 호출이라 별도 스레드로 — 그러지 않으면 로그인 창이
        # 열려 있는 동안 앱 전체가 멈춘 것처럼 보인다(초안 생성과 같은 문제).
        login_button.disabled = True
        login_button.update()
        login_status.value = "⏳ Chrome 창에서 네이버 로그인을 완료해주세요 (최대 2분 대기)…"
        login_status.color = BRAND_COLORS["text_muted"]
        login_status.update()

        def _work() -> None:
            # open_naver_login_session()은 실패를 {"success": False, ...}로
            # 돌려주지만, Playwright/Chrome 실행 자체가 터지면 예외를 던진다.
            # page.run_thread()로 돌린 함수 안에서 예외가 나면 Flet이 그걸
            # 조용히 삼켜버려서(콜백/로그 없음) 사용자 눈엔 "로그인 중…"에서
            # 영원히 멈춘 것처럼 보인다 — 반드시 여기서 잡아 화면에 띄워야 한다.
            try:
                result = open_naver_login_session(naver_blog_id)
            except Exception as exc:  # noqa: BLE001
                result = {"success": False, "message": f"❌ 로그인 실패: {exc}"}

            if result["success"]:
                refresh_all()
            else:
                login_status.value = result["message"]
                login_status.color = "#B3261E"
                login_button.disabled = False
                login_status.update()
                login_button.update()

        page.run_thread(_work)

    login_button.on_click = on_login

    info_children: list[ft.Control] = [
        ft.Text("아직 로그인하지 않았습니다. 게시하려면 먼저 로그인해주세요.", size=fs(12, scale), color="#8A6D3B"),
    ]
    if not naver_blog_id:
        info_children.append(ft.Text(
            "브랜드 킷에서 네이버 블로그 아이디를 먼저 등록해주세요.", size=fs(12, scale), color="#2F6B7A",
        ))
    info_children.append(ft.Text(
        "로그인 정보는 이 컴퓨터에만 저장되며, 언제든 [로그아웃]으로 지울 수 있습니다.",
        size=fs(10, scale), color=BRAND_COLORS["text_muted"],
    ))

    return ft.Column([
        ft.Text("🔑 네이버 로그인", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
        ft.Row([
            ft.Container(content=ft.Column(info_children, spacing=4), expand=3),
            ft.Column([login_button, login_status], expand=1),
        ]),
    ], spacing=8)


def _build_tabs(page: ft.Page, scale: float, brand_kit: dict, pending: list, published: list, refresh_all) -> ft.Control:
    if pending:
        pending_controls: list[ft.Control] = [
            _build_card(page, scale, brand_kit, c, "pending", "🚀 지금 게시", True, refresh_all) for c in pending
        ]
    else:
        pending_controls = [ft.Text(
            "게시 대기 중인 콘텐츠가 없습니다. 워크벤치에서 [네이버 게시 대기열로]를 눌러 보내세요.",
            size=fs(12, scale), color=BRAND_COLORS["text_muted"],
        )]

    published_controls: list[ft.Control] = []
    if published:
        published_controls.append(ft.Text(
            "네이버 창에서 [발행]을 누르지 못했다면 [다시 게시]로 같은 내용을 다시 불러올 수 있습니다. "
            "더 이상 필요 없는 글은 [삭제]로 목록에서 지우세요.",
            size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))
        published_controls += [
            _build_card(page, scale, brand_kit, c, "published", "🔁 다시 게시", False, refresh_all) for c in published
        ]
    else:
        published_controls = [ft.Text(
            "게시 완료된 콘텐츠가 없습니다.", size=fs(12, scale), color=BRAND_COLORS["text_muted"],
        )]

    return ft.Tabs(
        length=2,
        expand=True,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(tabs=[
                    ft.Tab(label=f"게시 대기 {len(pending)}"),
                    ft.Tab(label=f"게시 완료 {len(published)}"),
                ]),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        ft.Container(
                            content=ft.Column(pending_controls, spacing=10, scroll=ft.ScrollMode.AUTO),
                            padding=ft.Padding.only(top=10),
                        ),
                        ft.Container(
                            content=ft.Column(published_controls, spacing=10, scroll=ft.ScrollMode.AUTO),
                            padding=ft.Padding.only(top=10),
                        ),
                    ],
                ),
            ],
        ),
    )


def build(page: ft.Page, state: AppState) -> ft.Control:
    scale = state.font_scale
    brand_kit = repo.get_brand_kit()
    naver_blog_id = (brand_kit.get("naver_blog_id") or "").strip()

    login_box = ft.Container()
    lists_box = ft.Container(expand=True)

    def refresh_all() -> None:
        login_box.content = _build_login_section(page, scale, naver_blog_id, refresh_all)
        pending = repo.list_campaigns(statuses=["ready_to_publish"])
        published = repo.list_campaigns(statuses=["published"])
        lists_box.content = _build_tabs(page, scale, brand_kit, pending, published, refresh_all)
        login_box.update()
        lists_box.update()

    login_box.content = _build_login_section(page, scale, naver_blog_id, refresh_all)
    pending = repo.list_campaigns(statuses=["ready_to_publish"])
    published = repo.list_campaigns(statuses=["published"])
    lists_box.content = _build_tabs(page, scale, brand_kit, pending, published, refresh_all)

    def _fingerprint(campaigns: list[dict]) -> tuple:
        return tuple(sorted((c["id"], c.get("status"), c.get("publish_error"), c.get("updated_at")) for c in campaigns))

    # 이 화면을 여러 번 오갈 때마다(다른 메뉴 갔다가 다시 들어올 때마다
    # build()가 다시 실행된다) 새 폴링 스레드가 계속 쌓이는 걸 막기 위해,
    # page 자체에 "지금 몇 번째로 만들어진 화면인지" 세대 번호를 적어둔다.
    # 폴링 루프는 매 tick마다 자기 세대가 아직 최신인지 직접 확인하고, 아니면
    # 곧바로 멈춘다 — "컨트롤이 트리에서 빠지면 update()가 예외를 던질
    # 것이다"라는 가정에만 기대지 않는다(그 예외는 상태가 실제로 바뀌어
    # refresh_all()이 호출될 때만 발생하므로, 아무것도 안 바뀐 채 화면만
    # 떠난 경우엔 안 던져져서 예전 폴러가 계속 남아있었다).
    generation = getattr(page, "_naver_publish_poll_generation", 0) + 1
    page._naver_publish_poll_generation = generation
    initial_fingerprint = _fingerprint(pending) + _fingerprint(published)

    def _poll_for_publish_completion(last: tuple) -> None:
        # [지금 게시]는 백그라운드 스레드(naver_paste_worker.run)를 띄우고
        # 바로 반환한다 — 그 스레드가 publish_error(사진 누락 경고 등)를
        # 기록해도 이 화면과는 SQLite 말고는
        # 아무 연결이 없어서, 예전엔 사용자가 직접 다른 메뉴에 갔다 와야만
        # (=화면이 통째로 다시 그려져야만) 게시 대기 개수가 바뀌었다. 이 화면이
        # 떠 있는 동안은 대신 몇 초마다 스스로 다시 읽어서 바뀐 게 있으면
        # 새로 그린다.
        for _ in range(200):  # 최대 ~10분 — 화면을 계속 열어두고 방치하는 경우의 안전장치
            time.sleep(3)
            if getattr(page, "_naver_publish_poll_generation", None) != generation:
                return  # 다른 메뉴로 이동했다가 이 화면이 다시 만들어졌다 — 이전 세대는 멈춘다
            try:
                now_campaigns = repo.list_campaigns(statuses=["ready_to_publish", "published"])
                now_pending = [c for c in now_campaigns if c["status"] == "ready_to_publish"]
                now_published = [c for c in now_campaigns if c["status"] == "published"]
                now = _fingerprint(now_pending) + _fingerprint(now_published)
                if now != last:
                    last = now
                    refresh_all()
            except Exception:
                return

    # 폴링 루프가 pending/published 전체 리스트(생성된 본문 등 큰 JSON 컬럼
    # 포함)를 계속 참조하지 않도록, 그 지문(fingerprint)만 인자로 넘긴다.
    page.run_thread(_poll_for_publish_completion, initial_fingerprint)

    return ft.Column(
        [
            ft.Text("네이버 블로그 반자동 게시", size=fs(24, scale), weight=ft.FontWeight.BOLD),
            ft.Text(
                "[지금 게시]를 누르면 Chrome 창이 열리고 제목·본문·사진이 자동으로 입력됩니다. "
                "내용을 확인한 뒤 마지막 [발행] 버튼만 그 창에서 직접 눌러주세요.",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            ),
            login_box,
            ft.Divider(),
            lists_box,
        ],
        spacing=10, scroll=ft.ScrollMode.AUTO, expand=True,
    )
