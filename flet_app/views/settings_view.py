"""4개 탭 전부(LLM 벤더 / 네이버 API / 이미지 분석 /
사용량), 그리고 새로 추가된 글자 크기 조정 기능.

네이버 API 탭의 키워드 갱신 마법사는 4단계 상태머신인데, 그 상태(`stage`,
`candidates`, `scored`, `proposal`)는 원래부터 `core.repo.get_app_state`/
`set_app_state`로 **DB에 저장**된다 — Streamlit의 `st.session_state`가 아니다.
그래서 Flet 쪽도 새 상태관리를 만들지 않고, Streamlit의 "상태를 바꾸고
st.rerun()" 패턴을 그대로 흉내낸다: 마법사 섹션 전체를 `ft.Container` 하나에
담아두고, 각 단계 버튼이 로직 실행 → `set_app_state` 저장 → 그 컨테이너의
`content`를 통째로 다시 만들고 `.update()`. 나머지 3탭의 비즈니스 로직은
ai_workers.multi_llm_router / ai_workers.vision / ai_workers.keyword_research /
ai_workers.keyword_curator / core.repo에서 그대로 가져다 씁니다(원본 Streamlit
파일과 동일한 호출).

글자 크기 조정은 이 화면(⚙️ 설정)에서 고르지만 앱 전체에 적용됩니다 — 값은
`core.repo`의 범용 app_state 테이블에 저장되고, `flet_app/state.py`의
`AppState.font_scale` + `request_rerender`를 통해 페이지 테마와 현재 화면을
즉시 다시 그립니다. `flet_app/theme.py`의 `fs()`가 이 배율을 실제 숫자로
바꾼다.
"""
from __future__ import annotations

import flet as ft

from ai_workers import keyword_curator, keyword_research, vision
from ai_workers.multi_llm_router import VENDORS, test_connection, vision_capable_vendors
from core import repo
from core.crypto_utils import decrypt_api_key, encrypt_api_key

from flet_app.state import FONT_SCALE_STATE_KEY, AppState
from flet_app.theme import BRAND_COLORS, MAX_FONT_SCALE, MIN_FONT_SCALE, fs, stat_card


def _saved_badge(encrypted_blob: str, updated_at: str | None) -> str:
    try:
        fingerprint = decrypt_api_key(encrypted_blob)[-4:]
    except Exception:  # noqa: BLE001
        fingerprint = "????"
    when = f" · {updated_at} 저장" if updated_at else ""
    return f"✅ 등록됨 ****{fingerprint}{when}"


def _warn_box(text: str, scale: float, color: str = "#8A6D3B", bg: str = "#FFF6E5") -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=fs(12, scale), color=color),
        bgcolor=bg,
        padding=10,
        border_radius=8,
    )


# views/06_settings.py의 두 st.expander("📄 키 발급 방법 ...") 안 markdown을 그대로 옮김.
API_HUB_GUIDE_MD = """**NAVER API HUB — 블로그/뉴스/카페 검색 + 검색어트렌드**

1. `console.ncloud.com` 로그인 (네이버 클라우드 플랫폼 계정)
2. **Menu > All Services > Application Services > NAVER API HUB > Subscription** → 약관 동의
   · 종량제 유료 서비스라 결제수단 등록이 필요합니다
3. **Application > [Application 등록]**
   · API 카테고리에서 **검색**(블로그·뉴스·카페)과 **Data Lab**(검색어트렌드)을 모두 선택
   · 이름은 영문·숫자·하이픈 20자 이내
4. 목록에서 **[인증 정보]** → 아래 두 값을 복사
   · `X-NCP-APIGW-API-KEY-ID` → **Client ID**
   · `X-NCP-APIGW-API-KEY` → **Client Secret**
5. **[한도 및 알림]** 에서 일·월 상한과 70%/90% 알림을 걸어두세요

> ⚠️ **`developers.naver.com`의 키는 여기서 동작하지 않습니다.** 인증 헤더 이름이 아예 다릅니다.
> 이미 그쪽 키가 있어도 위 절차로 새로 발급받으셔야 합니다.

**"요청한 API가 이 Application에서 활성화되어 있지 않습니다" 오류가 나면**
→ 구독은 됐지만 3번의 API 선택이 빠진 것입니다. 콘솔에서 해당 Application의
햄버거 버튼 > **[Application 수정]** 으로 검색·Data Lab을 체크하세요."""

SEARCHAD_GUIDE_MD = """1. `searchad.naver.com` 회원가입
   · **개인 광고주·개인사업자·법인 모두 가능**합니다. 광고를 집행하지 않아도 됩니다
2. 로그인 후 **우측 상단 [광고시스템]** 클릭
   · ⚠️ 첫 화면(광고주센터 메인)에는 "도구" 탭이 **없습니다.** 이 버튼을 눌러
     `manage.searchad.naver.com`으로 들어가야 상단에 정보관리·보고서·도구 탭이 생깁니다
3. 상단 **도구 > API 사용 관리**
4. 처음이면 안내 화면만 보입니다. 가운데 **[네이버 검색광고 API 서비스 신청]** 버튼을 누르세요
   · 심사·승인 절차는 없습니다. 약관 동의하면 즉시 발급됩니다
5. 세 값을 복사
   · **CUSTOMER_ID** — 고객번호, 7자리 숫자 (로그인 ID가 아닙니다)
   · **액세스라이선스** — `01000000...`으로 시작하는 긴 문자열
   · **비밀키** — `AQAAAA...`로 시작하는 문자열

> 💡 **계정 책임자(마스터) 계정**으로 로그인해야 보입니다.
> 운영관리 권한만 위임받은 계정에서는 키가 표시되지 않습니다.

> 💡 4번에서 막히는 경우가 많습니다. "API 사용 관리"에 들어갔는데 키가 안 보인다면
> 아직 **서비스 신청 전**입니다."""


def _guide_toggle(label: str, markdown_text: str, scale: float) -> list[ft.Control]:
    """토글형 "키 발급 방법" 안내 — Streamlit의 st.expander 자리."""
    box = ft.Container(
        visible=False,
        padding=ft.Padding.only(top=8, bottom=8),
        content=ft.Markdown(
            value=markdown_text,
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        ),
    )

    def toggle(e: ft.Event) -> None:
        box.visible = not box.visible
        box.update()

    return [ft.TextButton(label, on_click=toggle), box]


def _build_font_scale_section(state: AppState, scale: float) -> ft.Control:
    slider = ft.Slider(
        min=MIN_FONT_SCALE,
        max=MAX_FONT_SCALE,
        divisions=7,
        value=scale,
        label="{value}배",
        expand=True,
    )
    readout = ft.Text(f"{scale:.2f}배", size=fs(13, scale), weight=ft.FontWeight.BOLD)

    def on_change_end(e: ft.Event) -> None:
        new_scale = round(slider.value, 2)
        readout.value = f"{new_scale:.2f}배"
        readout.update()
        state.font_scale = new_scale
        repo.set_app_state(FONT_SCALE_STATE_KEY, {"value": new_scale})
        if state.request_rerender:
            state.request_rerender()

    slider.on_change_end = on_change_end

    return ft.Container(
        content=ft.Column(
            [
                ft.Text("🔠 글자 크기", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
                ft.Text(
                    "화면 전체 글자 크기를 조정합니다. 저장되어 다음 실행에도 유지됩니다.",
                    size=fs(11, scale),
                    color=BRAND_COLORS["text_muted"],
                ),
                ft.Row([slider, readout]),
            ],
            spacing=6,
        ),
        border=ft.Border.all(1, "#14000000"),
        border_radius=10,
        padding=14,
    )


def _vendor_card(page: ft.Page, vendor_key: str, spec: dict, default_status: ft.Text, scale: float) -> ft.Control:
    saved = repo.get_llm_setting(vendor_key)

    model_field = ft.TextField(
        label="모델명", value=(saved or {}).get("model_name", spec["default_model"])
    )
    key_field = ft.TextField(
        label="🔒 API Key",
        password=True,
        can_reveal_password=True,
        hint_text="저장된 키가 있으면 비워두고 테스트해도 유지됩니다" if saved else "",
    )
    badge = ft.Text(
        _saved_badge(saved["encrypted_api_key"], saved.get("updated_at")) if saved else "",
        size=fs(11, scale),
        color=BRAND_COLORS["text_muted"],
        visible=bool(saved),
    )
    status = ft.Text("", size=fs(12, scale))
    delete_button = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, visible=bool(saved))

    def _refresh_default_status() -> None:
        current = repo.get_brand_kit().get("default_generation_vendor")
        setting = repo.get_llm_setting(current) if current else None
        if current and setting and setting.get("is_active"):
            cur_spec = VENDORS[current]
            default_status.value = f"{cur_spec['icon']} {cur_spec['label']} ({setting['model_name']}) 사용 중"
            default_status.color = "#1B6E3C"
        else:
            default_status.value = "아직 기본 생성 모델이 없습니다. 벤더 카드에서 [연결 테스트 · 저장]을 누르세요."
            default_status.color = "#B3261E"
        default_status.update()

    def on_test(e: ft.Event) -> None:
        nonlocal saved
        key_to_test = key_field.value or ""
        reuse_saved = False
        if not key_to_test and saved:
            key_to_test = decrypt_api_key(saved["encrypted_api_key"])
            reuse_saved = True

        if not key_to_test:
            status.value = "❌ API Key를 입력하세요."
            status.color = "#B3261E"
            status.update()
            return

        test_button.disabled = True
        test_button.update()
        status.value = "⏳ 연결 테스트 중…"
        status.color = BRAND_COLORS["text_muted"]
        status.update()

        def _work() -> None:
            nonlocal saved
            # 전체를 try/except/finally로 감싼다 — 원래는 test_connection()
            # 호출만 감쌌는데, 그 아래 DB 저장·복호화 쪽에서 예외가 나면(락,
            # 암호화 키 문제 등) run_thread 스레드가 조용히 죽어서 버튼이
            # "⏳ 연결 테스트 중…" 상태로 영원히 멈춘 것처럼 남았다.
            try:
                ok, message = test_connection(vendor_key, model_field.value, key_to_test)
                if not ok:
                    status.value = f"❌ {message}"
                    status.color = "#B3261E"
                    return

                encrypted = saved["encrypted_api_key"] if reuse_saved else encrypt_api_key(key_to_test)
                repo.upsert_llm_setting(vendor_key, model_field.value, encrypted)
                # 연결이 확인된 벤더를 그 자리에서 바로 기본 생성 모델로 지정합니다 —
                # 등록과 "기본으로 지정"이 별개 단계이면 소상공인 고객이 두 번째 단계를
                # 빠뜨리고 글쓰기 시점에야 원인을 알기 어려운 오류를 봅니다.
                repo.save_brand_kit(default_generation_vendor=vendor_key)

                saved = repo.get_llm_setting(vendor_key)
                badge.value = _saved_badge(saved["encrypted_api_key"], saved.get("updated_at"))
                badge.visible = True
                delete_button.visible = True
                key_field.value = ""
                status.value = f"✅ {message} · 기본 생성 모델로 지정했습니다."
                status.color = "#1B6E3C"
                badge.update()
                delete_button.update()
                key_field.update()
                _refresh_default_status()
            except Exception as exc:  # noqa: BLE001 — run_thread로 돌리므로 여기서 안 잡으면 조용히 사라진다
                status.value = f"❌ {exc}"
                status.color = "#B3261E"
            finally:
                test_button.disabled = False
                status.update()
                test_button.update()

        page.run_thread(_work)

    def on_delete(e: ft.Event) -> None:
        repo.delete_llm_setting(vendor_key)
        if repo.get_brand_kit().get("default_generation_vendor") == vendor_key:
            remaining = [
                k for k in VENDORS
                if k != vendor_key and (repo.get_llm_setting(k) or {}).get("is_active")
            ]
            repo.save_brand_kit(default_generation_vendor=remaining[0] if remaining else None)

        nonlocal saved
        saved = None
        badge.visible = False
        delete_button.visible = False
        status.value = "삭제했습니다."
        status.color = BRAND_COLORS["text_muted"]
        status.update()
        badge.update()
        delete_button.update()
        _refresh_default_status()

    test_button = ft.FilledButton("연결 테스트 · 저장", on_click=on_test, expand=True)
    delete_button.on_click = on_delete

    header = [ft.Text(f"{spec['icon']} {spec['label']}", weight=ft.FontWeight.BOLD, size=fs(14, scale))]
    if spec.get("note"):
        header.append(ft.Text(spec["note"], size=fs(11, scale), color=BRAND_COLORS["text_muted"]))

    return ft.Container(
        content=ft.Column(
            [
                *header,
                model_field,
                key_field,
                badge,
                ft.Row([test_button, delete_button]),
                status,
            ],
            spacing=8,
            tight=True,
        ),
        border=ft.Border.all(1, "#14000000"),
        border_radius=10,
        padding=14,
        expand=True,
    )


def _build_llm_tab(page: ft.Page, scale: float) -> ft.Control:
    default_status = ft.Text("", size=fs(13, scale))

    current = repo.get_brand_kit().get("default_generation_vendor")
    setting = repo.get_llm_setting(current) if current else None
    if current and setting and setting.get("is_active"):
        cur_spec = VENDORS[current]
        default_status.value = f"{cur_spec['icon']} {cur_spec['label']} ({setting['model_name']}) 사용 중"
        default_status.color = "#1B6E3C"
    else:
        default_status.value = "아직 기본 생성 모델이 없습니다. 아래 카드에서 [연결 테스트 · 저장]을 누르면 그 벤더가 바로 기본으로 지정됩니다."
        default_status.color = "#B3261E"

    cards = ft.Row(
        [_vendor_card(page, k, spec, default_status, scale) for k, spec in VENDORS.items()],
        spacing=12,
    )

    return ft.Column(
        [
            ft.Text(
                "입력한 키는 AES-256-GCM으로 암호화되어 로컬 SQLite에 저장됩니다.",
                size=fs(12, scale),
                color=BRAND_COLORS["text_muted"],
            ),
            cards,
            ft.Divider(),
            ft.Text("기본 생성 모델", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            ft.Text(
                "블로그 초안, 컴플라이언스 감사, SEO 재조정, 채널별 카피에 실제로 사용할 모델입니다.",
                size=fs(12, scale),
                color=BRAND_COLORS["text_muted"],
            ),
            default_status,
            ft.Text(
                "다른 벤더로 바꾸려면, 그 벤더 카드에서 [연결 테스트 · 저장]을 다시 누르세요.",
                size=fs(11, scale),
                color=BRAND_COLORS["text_muted"],
            ),
        ],
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def _build_api_hub_section(page: ft.Page, scale: float, rebuild) -> ft.Control:
    saved = repo.get_naver_api_settings()

    id_field = ft.TextField(
        label="Client ID",
        password=True,
        can_reveal_password=True,
        hint_text="저장된 값이 있으면 비워두고 테스트해도 유지됩니다" if saved else "",
        expand=True,
    )
    secret_field = ft.TextField(
        label="Client Secret",
        password=True,
        can_reveal_password=True,
        hint_text="저장된 값이 있으면 비워두고 테스트해도 유지됩니다" if saved else "",
        expand=True,
    )
    cap_field = ft.TextField(
        label="일일 호출 상한", value=str((saved or {}).get("daily_call_cap", 500))
    )
    badge = ft.Text(
        _saved_badge(saved["encrypted_client_secret"], saved.get("updated_at")) if saved else "",
        size=fs(11, scale), color=BRAND_COLORS["text_muted"], visible=bool(saved),
    )
    status = ft.Text("", size=fs(12, scale))
    delete_button = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, visible=bool(saved))
    stats_row = ft.Row(visible=bool(saved))

    def _refresh_stats() -> None:
        current = repo.get_naver_api_settings()
        if not current:
            stats_row.visible = False
            stats_row.controls = []
        else:
            cap = int(current.get("daily_call_cap") or 0)
            stats_row.visible = True
            stats_row.controls = [
                stat_card("오늘 호출", f"{repo.naver_calls_today():,}", f"상한 {cap:,}" if cap else "상한 없음", scale=scale),
                stat_card("남은 호출", f"{keyword_research.remaining_calls_today():,}" if cap else "—", scale=scale),
                stat_card("캐시된 키워드", f"{repo.keyword_cache_size():,}", "재조회 시 호출 0", scale=scale),
            ]
        stats_row.update()

    def on_test(e: ft.Event) -> None:
        nonlocal saved
        id_to_test = id_field.value or (decrypt_api_key(saved["encrypted_client_id"]) if saved else "")
        sec_to_test = secret_field.value or (decrypt_api_key(saved["encrypted_client_secret"]) if saved else "")
        if not (id_to_test and sec_to_test):
            status.value = "❌ Client ID와 Secret을 모두 입력하세요."
            status.color = "#B3261E"
            status.update()
            return

        # 저장이 먼저다: test_connection은 실제 과금되는 호출이라, 유효한 키가
        # 저장에 실패하면 다음 시도에서 또 한 번 값을 치르게 된다.
        try:
            cap = int(cap_field.value or 0)
        except ValueError:
            status.value = "❌ 일일 호출 상한은 숫자로 입력하세요."
            status.color = "#B3261E"
            status.update()
            return
        repo.save_naver_api_settings(encrypt_api_key(id_to_test), encrypt_api_key(sec_to_test), cap)

        test_button.disabled = True
        test_button.update()
        status.value = "⏳ 연결 테스트 중…"
        status.color = BRAND_COLORS["text_muted"]
        status.update()

        def _work() -> None:
            nonlocal saved
            try:
                ok, message = keyword_research.test_connection(id_to_test, sec_to_test)
                status.value = f"{'✅' if ok else '❌'} {message}"
                status.color = "#1B6E3C" if ok else "#B3261E"

                saved = repo.get_naver_api_settings()
                badge.value = _saved_badge(saved["encrypted_client_secret"], saved.get("updated_at"))
                badge.visible = True
                delete_button.visible = True
                id_field.value = ""
                secret_field.value = ""
                badge.update()
                delete_button.update()
                id_field.update()
                secret_field.update()
                _refresh_stats()
                # 아래 "SEO 키워드 새로 고르기" 마법사의 버튼은 이 탭이 처음 그려질 때의
                # keys_ok 값으로 disabled가 고정된다 — 여기서 키를 새로
                # 저장해도 마법사 쪽엔 반영이 안 돼서, 등록 직후엔 다른
                # 메뉴로 갔다 와야만 버튼이 눌리는 문제가 있었다. 저장에
                # 성공한 지금 마법사를 다시 그려서 바로 반영한다.
                rebuild()
            except Exception as exc:  # noqa: BLE001 — run_thread로 돌리므로 여기서 안 잡으면 조용히 사라진다
                status.value = f"❌ {exc}"
                status.color = "#B3261E"
            finally:
                test_button.disabled = False
                status.update()
                test_button.update()

        page.run_thread(_work)

    def on_delete(e: ft.Event) -> None:
        nonlocal saved
        repo.delete_naver_api_settings()
        saved = None
        badge.visible = False
        delete_button.visible = False
        status.value = "삭제했습니다."
        status.color = BRAND_COLORS["text_muted"]
        status.update()
        badge.update()
        delete_button.update()
        _refresh_stats()
        rebuild()

    delete_button.on_click = on_delete
    test_button = ft.FilledButton("연결 테스트 · 저장", on_click=on_test)

    if saved:
        cap = int((saved or {}).get("daily_call_cap") or 0)
        stats_row.controls = [
            stat_card("오늘 호출", f"{repo.naver_calls_today():,}", f"상한 {cap:,}" if cap else "상한 없음", scale=scale),
            stat_card("남은 호출", f"{keyword_research.remaining_calls_today():,}" if cap else "—", scale=scale),
            stat_card("캐시된 키워드", f"{repo.keyword_cache_size():,}", "재조회 시 호출 0", scale=scale),
        ]

    return ft.Column(
        [
            ft.Text("🔍 네이버 API HUB", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            ft.Text(
                "블로그·뉴스·카페 검색과 검색어트렌드를 담당합니다. 이 API는 유료 종량제입니다 — "
                "위 일일 상한은 이 앱이 스스로 지키는 방어선입니다.",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ),
            *_guide_toggle("📄 키 발급 방법 (처음이라면 여기부터)", API_HUB_GUIDE_MD, scale),
            _warn_box(
                "💳 이 API는 유료 종량제입니다. 아래 일일 상한은 이 앱이 스스로 지키는 방어선으로, "
                "상한에 도달하면 요청을 보내기 전에 차단합니다. NCP 콘솔의 [한도 및 알림]도 함께 걸어두세요.",
                scale,
            ),
            ft.Row([id_field, secret_field]),
            cap_field,
            badge,
            ft.Row([test_button, delete_button]),
            status,
            stats_row,
        ],
        spacing=8,
    )


def _build_searchad_section(page: ft.Page, scale: float, rebuild) -> ft.Control:
    saved = repo.get_searchad_settings()

    cid_field = ft.TextField(label="CUSTOMER_ID", hint_text="저장됨" if saved else "7자리 숫자", expand=True)
    key_field = ft.TextField(
        label="액세스라이선스", password=True, can_reveal_password=True,
        hint_text="저장됨" if saved else "", expand=True,
    )
    secret_field = ft.TextField(
        label="비밀키", password=True, can_reveal_password=True,
        hint_text="저장됨" if saved else "", expand=True,
    )
    badge = ft.Text(
        _saved_badge(saved["encrypted_secret_key"], saved.get("updated_at")) if saved else "",
        size=fs(11, scale), color=BRAND_COLORS["text_muted"], visible=bool(saved),
    )
    status = ft.Text("", size=fs(12, scale))
    delete_button = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, visible=bool(saved))

    def on_test(e: ft.Event) -> None:
        nonlocal saved
        cid = cid_field.value or (decrypt_api_key(saved["encrypted_customer_id"]) if saved else "")
        akey = key_field.value or (decrypt_api_key(saved["encrypted_api_key"]) if saved else "")
        asec = secret_field.value or (decrypt_api_key(saved["encrypted_secret_key"]) if saved else "")
        if not (cid and akey and asec):
            status.value = "❌ 세 값을 모두 입력하세요."
            status.color = "#B3261E"
            status.update()
            return

        test_button.disabled = True
        test_button.update()
        status.value = "⏳ 연결 테스트 중…"
        status.color = BRAND_COLORS["text_muted"]
        status.update()

        def _work() -> None:
            nonlocal saved
            try:
                ok, message = keyword_research.test_searchad_connection(cid, akey, asec)
                if not ok:
                    status.value = f"❌ {message}"
                    status.color = "#B3261E"
                    return

                repo.save_searchad_settings(encrypt_api_key(cid), encrypt_api_key(akey), encrypt_api_key(asec))
                status.value = f"✅ {message}"
                status.color = "#1B6E3C"

                saved = repo.get_searchad_settings()
                badge.value = _saved_badge(saved["encrypted_secret_key"], saved.get("updated_at"))
                badge.visible = True
                delete_button.visible = True
                cid_field.value = ""
                key_field.value = ""
                secret_field.value = ""
                badge.update()
                delete_button.update()
                cid_field.update()
                key_field.update()
                secret_field.update()
                # 아래 "SEO 키워드 새로 고르기" 마법사의 버튼은 이 탭이 처음 그려질 때의
                # keys_ok 값으로 disabled가 고정된다 — 여기서 키를 새로
                # 저장해도 마법사 쪽엔 반영이 안 돼서, 등록 직후엔 다른
                # 메뉴로 갔다 와야만 버튼이 눌리는 문제가 있었다. 저장에
                # 성공한 지금 마법사를 다시 그려서 바로 반영한다.
                rebuild()
            except Exception as exc:  # noqa: BLE001 — run_thread로 돌리므로 여기서 안 잡으면 조용히 사라진다
                status.value = f"❌ {exc}"
                status.color = "#B3261E"
            finally:
                test_button.disabled = False
                status.update()
                test_button.update()

        page.run_thread(_work)

    def on_delete(e: ft.Event) -> None:
        nonlocal saved
        repo.delete_searchad_settings()
        saved = None
        badge.visible = False
        delete_button.visible = False
        status.value = "삭제했습니다."
        status.color = BRAND_COLORS["text_muted"]
        status.update()
        badge.update()
        delete_button.update()
        rebuild()

    delete_button.on_click = on_delete
    test_button = ft.FilledButton("연결 테스트 · 저장", on_click=on_test)

    return ft.Column(
        [
            ft.Text("📊 검색광고 API (광고주센터)", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            ft.Text(
                "절대 월간 검색량과 연관키워드 발굴을 담당합니다. 완전 무료이고 위 일일 상한과 무관합니다.",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ),
            *_guide_toggle("📄 키 발급 방법 (CUSTOMER_ID · 액세스라이선스 · 비밀키)", SEARCHAD_GUIDE_MD, scale),
            ft.Row([cid_field, key_field, secret_field]),
            badge,
            ft.Row([test_button, delete_button]),
            status,
        ],
        spacing=8,
    )


def _build_sweep_wizard(page: ft.Page, scale: float, rebuild) -> ft.Control:
    naver_saved = repo.get_naver_api_settings()
    ad_saved = repo.get_searchad_settings()
    keys_ok = bool(naver_saved and ad_saved)
    pool = repo.get_brand_kit().get("seo_keywords") or []

    controls: list[ft.Control] = []

    if not keys_ok:
        controls.append(_warn_box("위에서 두 API 키를 모두 등록하면 아래 버튼이 활성화됩니다.", scale))

    maintain: list[ft.Control] = []
    if not pool:
        maintain.append(ft.Text("아직 SEO 키워드가 없습니다. 위 [🔑 SEO 키워드 새로 고르기]를 먼저 끝까지(4단계 적용) 진행하세요.", size=fs(12, scale)))
    else:
        freshness = keyword_research.pool_freshness(pool)
        if freshness["stale"]:
            maintain.append(_warn_box(
                f"📉 경쟁도 만료 {len(freshness['stale'])}/{len(pool)}개 — 아래 [🔄 숫자만 새로 재기]를 눌러주세요.",
                scale, color="#B3261E", bg="#FDECEA",
            ))
        else:
            maintain.append(ft.Text(
                f"✅ 키워드 {len(pool)}개 모두 측정돼 있습니다 · "
                f"{freshness['newest_document_age']:.0f}일 전 측정 · {freshness['days_left']:.0f}일 후 만료",
                size=fs(12, scale), color="#1B6E3C",
            ))

        need = keyword_research.pool_refresh_cost(pool)
        refresh_status = ft.Text("", size=fs(12, scale))

        def on_refresh(e: ft.Event) -> None:
            refresh_button.disabled = True
            refresh_button.update()
            refresh_status.value = "⏳ 재측정하는 중…"
            refresh_status.color = BRAND_COLORS["text_muted"]
            refresh_status.update()

            def _work() -> None:
                try:
                    result = keyword_research.refresh_pool(pool)
                except Exception as exc:  # noqa: BLE001
                    refresh_status.value = f"❌ {exc}"
                    refresh_status.color = "#B3261E"
                    refresh_button.disabled = not keys_ok
                    refresh_status.update()
                    refresh_button.update()
                    return
                refresh_status.value = f"{result['billed']}개 재측정 완료 ({keyword_research.DOCUMENT_CACHE_DAYS}일간 유효)."
                refresh_status.color = "#1B6E3C"
                refresh_status.update()
                rebuild()

            page.run_thread(_work)

        refresh_button = ft.FilledButton(
            f"🔄 숫자만 새로 재기 ({need}회 호출)" if need else "🔄 숫자만 새로 재기 (전부 최신 · 0회)",
            on_click=on_refresh,
            disabled=(not need) or (not keys_ok),
        )
        maintain.append(ft.Row([refresh_button, refresh_status]))


    seed_field = ft.TextField(
        label="씨앗 키워드 (쉼표로 최대 5개)",
        expand=True,
        helper="브랜드 이름이 아니라 네이버에서 실제로 검색되는 일반적인 말이어야 합니다.",
    )
    seed_error = ft.Text("", size=fs(12, scale), color="#B3261E")

    def on_suggest(e: ft.Event) -> None:
        suggest_button.disabled = True
        suggest_button.update()
        seed_error.value = "⏳ 추천 받는 중…"
        seed_error.color = BRAND_COLORS["text_muted"]
        seed_error.update()

        def _work() -> None:
            try:
                suggested = keyword_curator.suggest_seeds()
            except Exception as exc:  # noqa: BLE001
                seed_error.value = f"추천 실패: {exc}"
                seed_error.color = "#B3261E"
                suggest_button.disabled = False
                seed_error.update()
                suggest_button.update()
                return
            if suggested:
                seed_field.value = ", ".join(suggested)
                seed_error.value = ""
            else:
                seed_error.value = "추천 결과가 비어 있습니다. 직접 입력해주세요."
                seed_error.color = "#B3261E"
            suggest_button.disabled = False
            seed_field.update()
            seed_error.update()
            suggest_button.update()

        page.run_thread(_work)

    suggest_button = ft.OutlinedButton("🪄 브랜드에서 추천", on_click=on_suggest)
    controls.append(ft.Row([seed_field, suggest_button]))
    controls.append(seed_error)

    sweep = repo.get_app_state("sweep") or {}
    stage = sweep.get("stage", 0)
    age = repo.app_state_age_days("sweep")
    if stage and age is not None and age > 0.02:
        controls.append(ft.Text(
            f"↩️ 진행 중이던 작업을 이어서 표시합니다 ({age * 24:.0f}시간 전 중단).",
            size=fs(11, scale), color=BRAND_COLORS["text_muted"],
        ))

    steps = ["1. 후보 찾기", "2. 경쟁도 조사", "3. 브랜드 판정", "4. 적용"]
    controls.append(ft.Row([
        ft.Container(
            content=ft.Text(
                s, size=fs(11, scale),
                color=ft.Colors.WHITE if i < stage else BRAND_COLORS["text_muted"],
            ),
            bgcolor=BRAND_COLORS["primary"] if i < stage else "#EDE6DD",
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        )
        for i, s in enumerate(steps)
    ]))

    vol_min_field = ft.TextField(label="최소 검색량", value=str(keyword_research.DEFAULT_MIN_VOLUME), width=140)
    vol_max_field = ft.TextField(label="최대 검색량", value=str(keyword_research.DEFAULT_MAX_VOLUME), width=140)
    cap_slider = ft.Slider(min=10, max=200, divisions=19, value=100, label="{value}개")
    adv_status = ft.Text("", size=fs(12, scale))

    def on_clear_kw_cache(e: ft.Event) -> None:
        removed = repo.clear_keyword_cache()
        adv_status.value = f"{removed}건을 삭제했습니다. 다음 조회는 전부 실제 호출을 씁니다."
        adv_status.update()

    def on_reset_sweep(e: ft.Event) -> None:
        repo.clear_app_state("sweep")
        rebuild()

    advanced_box = ft.Container(
        visible=False,
        padding=ft.Padding.only(top=8),
        content=ft.Column(
            [
                ft.Row([vol_min_field, vol_max_field]),
                ft.Text(f"조사할 후보 수 (= 최대 유료 호출 수): {int(cap_slider.value)}개", size=fs(12, scale)),
                cap_slider,
                ft.Text(
                    f"오늘 남은 호출 {keyword_research.remaining_calls_today():,}회",
                    size=fs(11, scale), color=BRAND_COLORS["text_muted"],
                ),
                ft.Row([
                    ft.OutlinedButton("🧹 키워드 캐시 비우기", on_click=on_clear_kw_cache),
                    *([ft.OutlinedButton("↩️ 진행 중인 작업 버리기", on_click=on_reset_sweep)] if stage else []),
                ]),
                adv_status,
            ],
            spacing=8,
        ),
    )

    def toggle_advanced(e: ft.Event) -> None:
        advanced_box.visible = not advanced_box.visible
        advanced_box.update()

    controls.append(ft.TextButton("⚙️ 고급 설정 (건드리지 않아도 됩니다)", on_click=toggle_advanced))
    controls.append(advanced_box)

    find_status = ft.Text("", size=fs(12, scale), color="#B3261E")

    def on_find(e: ft.Event) -> None:
        seeds = [s.strip() for s in (seed_field.value or "").split(",") if s.strip()]
        if not seeds:
            find_status.value = "씨앗 키워드를 채우세요. [🪄 브랜드에서 추천]을 눌러도 됩니다."
            find_status.update()
            return
        try:
            min_v = int(vol_min_field.value or keyword_research.DEFAULT_MIN_VOLUME)
            max_v = int(vol_max_field.value or keyword_research.DEFAULT_MAX_VOLUME)
        except ValueError:
            find_status.value = "검색량 범위는 숫자로 입력하세요."
            find_status.color = "#B3261E"
            find_status.update()
            return

        find_button.disabled = True
        find_button.update()
        find_status.value = "⏳ 후보를 찾는 중…"
        find_status.color = BRAND_COLORS["text_muted"]
        find_status.update()

        def _work() -> None:
            try:
                found = keyword_research.sweep_candidates(seeds, min_volume=min_v, max_volume=max_v)
            except Exception as exc:  # noqa: BLE001
                find_status.value = f"❌ {exc}"
                find_status.color = "#B3261E"
                find_button.disabled = False
                find_status.update()
                find_button.update()
                return
            repo.set_app_state("sweep", {"stage": 1, "candidates": found})
            rebuild()

        page.run_thread(_work)

    find_button = ft.FilledButton("🔎 1단계 · 후보 찾기 (무료)", on_click=on_find, disabled=not keys_ok)
    controls.append(find_button)
    controls.append(find_status)

    candidates = sweep.get("candidates")
    cap_n = int(cap_slider.value)
    if stage >= 1 and candidates is not None:
        if not candidates:
            controls.append(ft.Text(
                "설정한 검색량 구간에 후보가 없습니다. 씨앗을 더 일반적인 검색어로 바꿔보세요.",
                size=fs(12, scale),
            ))
        else:
            if len(candidates) < 150:
                controls.append(_warn_box(
                    f"🌱 후보가 {len(candidates)}개뿐입니다. 씨앗 키워드에 수식어가 붙어 범위가 좁아졌을 "
                    "가능성이 큽니다 — 수식어 없는 카테고리 이름으로 바꿔보세요.",
                    scale,
                ))
            cost = keyword_research.estimate_sweep_cost(candidates, limit=cap_n)
            controls.append(ft.Text(
                f"후보 {len(candidates)}개 발견. 상위 {min(len(candidates), cap_n)}개를 조사하면 "
                f"유료 호출 {cost}회를 씁니다 (오늘 남은 {keyword_research.remaining_calls_today():,}회).",
                size=fs(12, scale), color="#1B6E3C",
            ))

            def on_score(e: ft.Event) -> None:
                score_button.disabled = True
                score_button.update()
                find_status.value = "⏳ 경쟁도를 조사하는 중…"
                find_status.color = BRAND_COLORS["text_muted"]
                find_status.update()

                def _work() -> None:
                    try:
                        scored = keyword_research.score_candidates(candidates, limit=cap_n)
                    except Exception as exc:  # noqa: BLE001
                        find_status.value = f"❌ {exc}"
                        find_status.color = "#B3261E"
                        score_button.disabled = False
                        find_status.update()
                        score_button.update()
                        return
                    repo.set_app_state("sweep", {"stage": 2, "candidates": candidates, "scored": scored})
                    rebuild()

                page.run_thread(_work)

            score_button = ft.FilledButton(
                f"💳 2단계 · 경쟁도 조사 ({cost}회 사용)", on_click=on_score, disabled=not keys_ok
            )
            controls.append(score_button)

    scored = sweep.get("scored")
    if stage >= 2 and scored:
        columns = ["키워드", "월간 검색량", "블로그 문서 수", "점수"]
        keys = ["keyword", "estimated_volume", "documents", "score"]
        controls.append(ft.Text(f"조사 결과 {len(scored)}개", weight=ft.FontWeight.BOLD, size=fs(13, scale)))
        controls.append(ft.Column([ft.DataTable(
            columns=[ft.DataColumn(ft.Text(c)) for c in columns],
            rows=[ft.DataRow(cells=[ft.DataCell(ft.Text(str(row.get(k) or ""))) for k in keys]) for row in scored],
        )], scroll=ft.ScrollMode.AUTO))

        propose_status = ft.Text("", size=fs(12, scale))

        def on_propose(e: ft.Event) -> None:
            propose_button.disabled = True
            propose_button.update()
            propose_status.value = "⏳ 브랜드에 맞게 고르는 중…"
            propose_status.color = BRAND_COLORS["text_muted"]
            propose_status.update()

            def _work() -> None:
                try:
                    result = keyword_curator.propose(scored, current=pool)
                except Exception as exc:  # noqa: BLE001
                    propose_status.value = f"❌ {exc}"
                    propose_status.color = "#B3261E"
                    propose_button.disabled = False
                    propose_status.update()
                    propose_button.update()
                    return
                repo.set_app_state("sweep", {**sweep, "stage": 3, "proposal": result})
                rebuild()

            page.run_thread(_work)

        propose_button = ft.FilledButton(
            "🤖 3단계 · 브랜드에 맞는 것만 고르기", on_click=on_propose, disabled=not keys_ok
        )
        controls.append(propose_button)
        controls.append(propose_status)

    proposal = sweep.get("proposal")
    revive_checkboxes: list[tuple] = []
    if stage >= 3 and proposal:
        if proposal.get("error"):
            controls.append(ft.Text(f"❌ 판정 실패: {proposal['error']}", color="#B3261E", size=fs(12, scale)))
        else:
            if proposal.get("focus"):
                controls.append(_warn_box(
                    f"🎯 주력 주제: {' · '.join(proposal['focus'])} — 이 축에서 벗어난 키워드는 "
                    "검색량이 커도 제외했습니다.",
                    scale, color="#1B6E3C", bg="#E8F5E9",
                ))

            for gkey, glabel in keyword_curator.INTENT_GROUPS.items():
                rows = proposal["groups"].get(gkey) or []
                if not rows:
                    continue
                note = (
                    " · 검색 타깃에서 제외됩니다 (본문에는 등장)" if gkey == "identity"
                    else f" · 전환 가중치 {keyword_curator.GROUP_DEFAULT_WEIGHT.get(gkey)} 부여"
                )
                controls.append(ft.Text(f"[{glabel}]{note}", weight=ft.FontWeight.BOLD, size=fs(12, scale)))
                for r in rows:
                    mark = "=" if r["keyword"] in proposal["kept"] else "+"
                    vol = f"{r['estimated_volume']:,}" if r.get("estimated_volume") else "—"
                    ratio = f" · 경쟁 {r['ratio']:.0f}배" if r.get("ratio") else ""
                    controls.append(ft.Text(
                        f"{mark} {r['keyword']}  {vol}{ratio}  {r.get('reason', '')}", size=fs(11, scale)
                    ))

            def _ratio_of(row: dict) -> float | None:
                vol, docs = row.get("estimated_volume"), row.get("documents")
                return (docs / vol) if (vol and docs) else None

            excluded = sorted(
                proposal["excluded"],
                key=lambda r: _ratio_of(r) if _ratio_of(r) is not None else 9e9,
            )
            if excluded:
                controls.append(ft.Text(
                    "[제외됨 — 되살릴 수 있습니다] 경쟁이 낮은 순입니다.",
                    weight=ft.FontWeight.BOLD, size=fs(12, scale),
                ))

                def _revive_row(row: dict) -> ft.Checkbox:
                    vol = f"{row['estimated_volume']:,}" if row.get("estimated_volume") else "—"
                    ratio = _ratio_of(row)
                    label = (
                        f"{row['keyword']} — 검색 {vol}"
                        + (f" · 경쟁 {ratio:.0f}배" if ratio is not None else " · 경쟁 미측정")
                        + f" · {row.get('reason', '')}"
                    )
                    cb = ft.Checkbox(label=label)
                    revive_checkboxes.append((row["keyword"], cb))
                    return cb

                for row in excluded[:15]:
                    controls.append(_revive_row(row))
                rest = excluded[15:]
                if rest:
                    rest_column = ft.Column([_revive_row(row) for row in rest], visible=False)

                    def toggle_rest(e: ft.Event) -> None:
                        rest_column.visible = not rest_column.visible
                        rest_column.update()

                    controls.append(ft.TextButton(f"나머지 {len(rest)}개 보기/숨기기", on_click=toggle_rest))
                    controls.append(rest_column)

            apply_status = ft.Text("", size=fs(12, scale))

            def on_apply(e: ft.Event) -> None:
                revived = [kw for kw, cb in revive_checkboxes if cb.value]
                final = proposal["accepted"] + revived

                apply_button.disabled = True
                apply_button.update()
                apply_status.value = "⏳ 적용하고 측정하는 중…"
                apply_status.color = BRAND_COLORS["text_muted"]
                apply_status.update()

                def _work() -> None:
                    try:
                        applied = keyword_curator.apply_and_measure(final, proposal)
                        repo.clear_app_state("sweep")
                        apply_status.value = (
                            f"✅ SEO 키워드 {applied['pool']}개로 갱신하고 측정까지 마쳤습니다 "
                            f"(호출 {applied['calls']}회)."
                        )
                        apply_status.color = "#1B6E3C"
                        apply_status.update()
                        rebuild()
                    except Exception as exc:  # noqa: BLE001 — run_thread로 돌리므로 여기서 안 잡으면 조용히 사라진다
                        apply_status.value = f"❌ {exc}"
                        apply_status.color = "#B3261E"
                        apply_button.disabled = False
                        apply_status.update()
                        apply_button.update()

                page.run_thread(_work)

            def on_discard(e: ft.Event) -> None:
                repo.clear_app_state("sweep")
                rebuild()

            controls.append(ft.Text(
                f"적용하면 최소 {len(proposal['accepted'])}개 — 유지 {len(proposal['kept'])} · "
                f"추가 {len(proposal['added'])} · 제거 {len(proposal['removed'])} "
                "(위에서 되살린 항목은 여기에 더해집니다)",
                size=fs(12, scale),
            ))
            apply_button = ft.FilledButton("✅ 4단계 · 적용", on_click=on_apply, disabled=not keys_ok)
            controls.append(ft.Row([
                apply_button,
                ft.OutlinedButton("무시하고 버리기", on_click=on_discard),
            ]))
            controls.append(apply_status)

    # 두 기능을 제목부터 나눕니다. 예전엔 둘이 "키워드 갱신" 한 제목 아래 있었고
    # [숫자만 새로 재기]가 1~4단계 위에 놓여 있어서, "갱신"이 1~4단계와 별개의
    # (앞이나 뒤에 하는) 작업처럼 읽혔습니다. 실제로는 4단계 [적용]이 곧 SEO
    # 키워드 갱신이고, 숫자 재기는 목록을 건드리지 않는 유지 작업입니다.
    pick = ft.Column(
        [
            ft.Text("🔑 SEO 키워드 새로 고르기 (추천 → 1~4단계)", weight=ft.FontWeight.BOLD, size=fs(16, scale)),
            ft.Text(
                "브랜드 킷의 SEO 키워드를 바꾸는 곳은 여기 하나뿐입니다. 4단계 [✅ 적용]을 눌러야 "
                "바뀌고, 적용하면서 새 키워드의 수치도 함께 측정하므로 따로 할 일은 없습니다. "
                "처음 한 번, 그리고 사업 방향이 바뀌거나 몇 달에 한 번 새 검색어를 반영하고 싶을 때 돌리세요.",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            ),
            *controls,
        ],
        spacing=8,
    )
    keep = ft.Column(
        [
            ft.Text("🔄 현재 키워드 수치 유지 (30일마다)", weight=ft.FontWeight.BOLD, size=fs(16, scale)),
            ft.Text(
                "키워드 목록은 그대로 두고 검색량·경쟁도 숫자만 다시 잽니다. 측정값은 30일 동안만 유효하고, "
                "만료되면 글마다 타깃 키워드를 고르는 점수가 0으로 계산됩니다. 만료 경고가 뜰 때 누르세요.",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            ),
            *maintain,
        ],
        spacing=8,
    )
    return ft.Column([pick, ft.Divider(), keep], spacing=12)


def _build_keyword_diagnostic_section(page: ft.Page, scale: float) -> ft.Control:
    pool = repo.get_brand_kit().get("seo_keywords") or []
    naver_saved = repo.get_naver_api_settings()
    result_box = ft.Container()
    status = ft.Text("", size=fs(12, scale), color="#B3261E")

    def on_run(e: ft.Event) -> None:
        run_button.disabled = True
        run_button.update()
        status.value = "⏳ 진단하는 중…"
        status.color = BRAND_COLORS["text_muted"]
        status.update()

        def _work() -> None:
            try:
                ranked = keyword_research.rank_keywords(pool)
            except Exception as exc:  # noqa: BLE001
                status.value = f"❌ {exc}"
                status.color = "#B3261E"
                run_button.disabled = False
                status.update()
                run_button.update()
                return
            status.value = ""
            run_button.disabled = False
            status.update()
            run_button.update()
            if not ranked:
                result_box.content = ft.Text("결과가 없습니다.", size=fs(12, scale))
            else:
                columns = ["키워드", "월간 검색량", "트렌드(상대)", "블로그 문서 수", "점수"]
                keys = ["keyword", "estimated_volume", "demand", "documents", "score"]
                result_box.content = ft.Column([ft.DataTable(
                    columns=[ft.DataColumn(ft.Text(c)) for c in columns],
                    rows=[
                        ft.DataRow(cells=[ft.DataCell(ft.Text(str(row.get(k) or ""))) for k in keys])
                        for row in ranked
                    ],
                )], scroll=ft.ScrollMode.AUTO)
            result_box.update()

        page.run_thread(_work)

    run_button = ft.OutlinedButton("키워드 진단 실행", on_click=on_run)

    body: list[ft.Control] = [
        ft.Text(
            "🔬 지금 키워드가 얼마나 좋은지만 보기 (아무것도 바뀌지 않습니다)",
            weight=ft.FontWeight.BOLD, size=fs(13, scale),
        )
    ]
    if not pool:
        body.append(ft.Text("브랜드 킷에 SEO 키워드가 없습니다.", size=fs(12, scale)))
    elif not naver_saved:
        body.append(ft.Text("먼저 위에서 키를 등록하세요.", size=fs(12, scale)))
    else:
        body.append(ft.Text(f"대상 {len(pool)}개: {', '.join(pool)}", size=fs(12, scale)))
        body.append(run_button)
        body.append(status)
        body.append(result_box)
    return ft.Column(body, spacing=8)


def _build_naver_tab(page: ft.Page, scale: float) -> ft.Control:
    wizard_box = ft.Container()

    def rebuild() -> None:
        wizard_box.content = _build_sweep_wizard(page, scale, rebuild)
        wizard_box.update()

    wizard_box.content = _build_sweep_wizard(page, scale, rebuild)

    return ft.Column(
        [
            ft.Text(
                "블로그·뉴스·카페 검색과 검색어트렌드, 절대 검색량·연관키워드 발굴을 담당합니다.",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            ),
            _build_api_hub_section(page, scale, rebuild),
            ft.Divider(),
            _build_searchad_section(page, scale, rebuild),
            ft.Divider(),
            wizard_box,
            ft.Divider(),
            _build_keyword_diagnostic_section(page, scale),
        ],
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def _build_vision_tab(scale: float) -> ft.Control:
    brand_kit = repo.get_brand_kit()

    vision_enabled = ft.Switch(
        label="이미지 분석 사용",
        value=brand_kit.get("vision_enabled", True),
    )

    registered = [
        v for v in vision_capable_vendors() if (repo.get_llm_setting(v) or {}).get("is_active")
    ]
    default_label = "(기본 생성 모델과 동일)"
    current_vv = brand_kit.get("vision_vendor")
    vendor_dropdown = ft.Dropdown(
        label="이미지 분석 벤더",
        value=current_vv if current_vv in registered else default_label,
        options=[ft.DropdownOption(key=default_label, text=default_label)]
        + [ft.DropdownOption(key=v, text=f"{VENDORS[v]['icon']} {VENDORS[v]['label']}") for v in registered],
        expand=True,
    )

    quality_keys = list(vision.QUALITY_PRESETS.keys())
    current_q = brand_kit.get("vision_quality") or vision.DEFAULT_QUALITY
    quality_group = ft.RadioGroup(
        value=current_q if current_q in quality_keys else quality_keys[0],
        content=ft.Column(
            [
                ft.Radio(
                    value=k,
                    label=f"{vision.QUALITY_PRESETS[k]['label']} · {vision.QUALITY_PRESETS[k]['max_edge']}px",
                )
                for k in quality_keys
            ]
        ),
    )
    quality_hint = ft.Text(
        vision.QUALITY_PRESETS[current_q]["hint"] if current_q in quality_keys else "",
        size=fs(11, scale),
        color=BRAND_COLORS["text_muted"],
    )

    def on_quality_change(e: ft.Event) -> None:
        preset = vision.QUALITY_PRESETS.get(quality_group.value)
        quality_hint.value = preset["hint"] if preset else ""
        quality_hint.update()

    quality_group.on_change = on_quality_change

    save_status = ft.Text("", size=fs(12, scale), color="#1B6E3C")

    def on_save(e: ft.Event) -> None:
        chosen = vendor_dropdown.value
        repo.save_brand_kit(
            vision_enabled=vision_enabled.value,
            vision_quality=quality_group.value,
            vision_vendor=None if chosen == default_label else chosen,
        )
        save_status.value = "✅ 저장했습니다."
        save_status.update()

    save_button = ft.FilledButton("이미지 분석 설정 저장", icon=ft.Icons.SAVE, on_click=on_save)

    # --- 벤더별 토큰 비용 표 ---
    cost_columns = ["벤더"] + [
        f"{preset['label']} ({preset['max_edge']}px)" for preset in vision.QUALITY_PRESETS.values()
    ] + ["원본 3024x4032 (미축소)"]
    cost_rows = []
    for v in ["google", "openai", "anthropic"]:
        cells = [ft.DataCell(ft.Text(v))]
        for preset in vision.QUALITY_PRESETS.values():
            edge = preset["max_edge"]
            cells.append(ft.DataCell(ft.Text(str(vision.estimate_image_tokens(v, edge, edge)))))
        cells.append(ft.DataCell(ft.Text(str(vision.estimate_image_tokens(v, 3024, 4032)))))
        cost_rows.append(ft.DataRow(cells=cells))
    cost_table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(c)) for c in cost_columns],
        rows=cost_rows,
    )

    cached_count = repo.vision_cache_size()
    cache_stat = stat_card("캐시된 사진 캡션", f"{cached_count:,}", "재사용 시 추가 토큰 0", scale=scale)
    clear_status = ft.Text("", size=fs(12, scale))

    def on_clear_cache(e: ft.Event) -> None:
        removed = repo.clear_vision_cache()
        clear_status.value = f"{removed}건을 삭제했습니다."
        clear_status.update()

    return ft.Column(
        [
            ft.Text(
                "사진 분석 토큰 최소화: 캡션 캐시 · 메모 분리 프롬프트 · 벤더별 최소 과금 해상도 · "
                "배치 호출 · 출력 상한.",
                size=fs(12, scale),
                color=BRAND_COLORS["text_muted"],
            ),
            ft.Row([vision_enabled]),
            vendor_dropdown,
            ft.Divider(),
            ft.Text("분석 화질", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            quality_group,
            quality_hint,
            ft.Row([save_button, save_status]),
            ft.Divider(),
            ft.Text("벤더별 사진 1장 토큰 비용", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            cost_table,
            ft.Divider(),
            ft.Row([cache_stat, ft.OutlinedButton("🧹 캐시 비우기", on_click=on_clear_cache)]),
            clear_status,
        ],
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def _build_usage_tab(scale: float) -> ft.Control:
    totals = repo.usage_totals()
    spent = totals["input_tokens"] + totals["output_tokens"]
    saved = totals["saved_tokens"]

    stats = ft.Row(
        [
            stat_card("총 입력 토큰", f"{totals['input_tokens']:,}", scale=scale),
            stat_card("총 출력 토큰", f"{totals['output_tokens']:,}", scale=scale),
            stat_card("절약된 토큰", f"{saved:,}", "캐시 적중 + 해상도 축소 + 배치", scale=scale),
            stat_card("캐시 적중", f"{totals['cache_hits']:,}", f"분석한 사진 {totals['images']:,}장", scale=scale),
        ],
        spacing=12,
    )

    progress_section: list[ft.Control] = []
    if saved + spent:
        ratio = saved / (saved + spent)
        progress_section = [
            ft.ProgressBar(value=ratio, width=400),
            ft.Text(f"절감률 {ratio * 100:.1f}%", size=fs(12, scale)),
        ]

    recent = repo.recent_usage(50)
    if recent:
        columns = ["시각", "종류", "벤더", "모델", "사진", "입력", "출력", "절약", "메모"]
        keys = ["ts", "kind", "vendor", "model", "image_count", "est_input_tokens", "est_output_tokens", "est_saved_tokens", "note"]
        usage_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text(c)) for c in columns],
            rows=[
                ft.DataRow(cells=[ft.DataCell(ft.Text(str(row.get(k) or ""))) for k in keys])
                for row in recent
            ],
        )
        usage_section: ft.Control = ft.Column([usage_table], scroll=ft.ScrollMode.AUTO)
    else:
        usage_section = ft.Text("아직 호출 기록이 없습니다.", color=BRAND_COLORS["text_muted"])

    return ft.Column(
        [
            stats,
            *progress_section,
            ft.Text(
                "입력/출력 토큰은 각 프로바이더가 응답에 실어 보낸 실제 usage 값입니다. "
                "절약분은 저장된 사진을 축소·캐시·배치 없이 그대로 보냈을 때와의 차이입니다.",
                size=fs(11, scale),
                color=BRAND_COLORS["text_muted"],
            ),
            ft.Divider(),
            ft.Text("최근 호출", weight=ft.FontWeight.BOLD, size=fs(14, scale)),
            usage_section,
        ],
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def build(page: ft.Page, state: AppState) -> ft.Control:
    scale = state.font_scale
    return ft.Column(
        [
            ft.Text("설정 · 토큰", size=fs(24, scale), weight=ft.FontWeight.BOLD),
            _build_font_scale_section(state, scale),
            ft.Tabs(
                length=4,
                expand=True,
                content=ft.Column(
                    expand=True,
                    controls=[
                        ft.TabBar(
                            tabs=[
                                ft.Tab(label="🔑 LLM 벤더"),
                                ft.Tab(label="🔍 네이버 API"),
                                ft.Tab(label="🖼️ 이미지 분석 (토큰)"),
                                ft.Tab(label="📈 사용량"),
                            ]
                        ),
                        ft.TabBarView(
                            expand=True,
                            controls=[
                                ft.Container(content=_build_llm_tab(page, scale), padding=10),
                                ft.Container(content=_build_naver_tab(page, scale), padding=10),
                                ft.Container(content=_build_vision_tab(scale), padding=10),
                                ft.Container(content=_build_usage_tab(scale), padding=10),
                            ],
                        ),
                    ],
                ),
            ),
        ],
        expand=True,
        spacing=10,
    )
