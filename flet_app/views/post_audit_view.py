"""📋 글 진단 — 직접 쓴 블로그 글을 발행 전에 점검하는 화면.

ai_workers/post_audit.py가 하는 일을 그대로 보여준다. 글을 고쳐 쓰지는
않는다 — 담당자가 쓴 글이므로, 무엇을 고치면 검색 노출이 달라지는지만 알린다.

뉴스 큐레이션 검색과 같은 이유로 제너레이터 핸들러를 쓴다: '진단 중' 표시를
먼저 그리고(yield), 그다음 네이버 API·AI 호출을 하는 것이 이 Flet 버전에서
중간 갱신을 확실히 보여주는 방법이다(news_curation_view.on_search 참고).
"""
from __future__ import annotations

import flet as ft

from ai_workers import post_audit
from flet_app.state import AppState
from flet_app.theme import BRAND_COLORS, fs


def _box(text: str, scale: float, bg: str, color: str) -> ft.Container:
    return ft.Container(content=ft.Text(text, size=fs(12, scale), color=color), bgcolor=bg, padding=10, border_radius=8)


def _keyword_table(rows: list[dict], scale: float) -> ft.Control:
    header = ft.Row([
        ft.Text("키워드", width=150, weight=ft.FontWeight.BOLD, size=fs(11, scale)),
        ft.Text("월 검색", width=80, weight=ft.FontWeight.BOLD, size=fs(11, scale)),
        ft.Text("블로그 글", width=100, weight=ft.FontWeight.BOLD, size=fs(11, scale)),
        ft.Text("검색당 글", width=80, weight=ft.FontWeight.BOLD, size=fs(11, scale)),
        ft.Text("판단", weight=ft.FontWeight.BOLD, size=fs(11, scale)),
    ])
    lines = [header]
    for r in rows:
        lines.append(ft.Row([
            ft.Text(r["keyword"], width=150, size=fs(11, scale)),
            ft.Text(f"{r['volume']:,}", width=80, size=fs(11, scale)),
            ft.Text(f"{r['documents']:,}" if r["documents"] is not None else "-", width=100, size=fs(11, scale)),
            ft.Text(f"{r['ratio']}" if r["ratio"] is not None else "-", width=80, size=fs(11, scale)),
            ft.Text(post_audit.VERDICT_LABELS.get(r["verdict"], r["verdict"]), size=fs(11, scale)),
        ]))
    return ft.Column(lines, spacing=4)


def _render(report: dict, scale: float) -> list[ft.Control]:
    t = report["text"]
    out: list[ft.Control] = [ft.Text("진단 결과", size=fs(18, scale), weight=ft.FontWeight.BOLD)]

    advice = report.get("advice") or []
    if advice:
        out.append(ft.Text("✏️ 고치면 좋은 점", size=fs(14, scale), weight=ft.FontWeight.BOLD))
        out += [ft.Text(f"{i}. {a}", size=fs(12, scale)) for i, a in enumerate(advice, 1)]
    else:
        out.append(_box("✅ 눈에 띄는 문제가 없습니다.", scale, "#E7F5EC", "#1B6E3C"))

    if report.get("api_error"):
        out.append(_box(
            f"네이버 API를 쓰지 못해 키워드 측정은 건너뛰었습니다: {report['api_error']} "
            "(⚙️ 설정에서 네이버 검색·검색광고 키를 확인하세요)",
            scale, "#FFF4E5", "#8A5A00",
        ))
    if report.get("title_keywords"):
        out += [
            ft.Divider(),
            ft.Text("🔎 제목에 쓴 말은 실제로 검색될까?", size=fs(14, scale), weight=ft.FontWeight.BOLD),
            ft.Text(
                "검색당 글 = 월 검색 1회당 이미 있는 블로그 글 수. 작을수록 상위 노출 경쟁이 덜합니다 "
                f"(약 {post_audit.EASY_RATIO} 이하 유리, {post_audit.HARD_RATIO} 초과 어려움).",
                size=fs(11, scale), color=BRAND_COLORS["text_muted"],
            ),
            _keyword_table(report["title_keywords"], scale),
        ]
    if report.get("suggestions"):
        out += [
            ft.Divider(),
            ft.Text("💡 이 글에 맞는, 노려볼 만한 검색어", size=fs(14, scale), weight=ft.FontWeight.BOLD),
            _keyword_table(report["suggestions"], scale),
        ]
    elif report.get("suggest_error"):
        out.append(ft.Text(f"추천 키워드를 만들지 못했습니다: {report['suggest_error']}", size=fs(11, scale)))

    sent = t.get("sentences") or {}
    out += [
        ft.Divider(),
        ft.Text("📏 본문", size=fs(14, scale), weight=ft.FontWeight.BOLD),
        ft.Text(
            f"글 {t['chars']:,}자(공백 제외)"
            + (f" · 사진 {t['photo_count']}장 · 사진당 {t['chars_per_photo']}자" if t.get("photo_count") else "")
            + f" · 소제목·번호 구획 {t['sections']}개"
            + f" · 평균 문장 {sent.get('avg', 0)}자 · 60자 넘는 문장 {sent.get('long', 0)}개",
            size=fs(12, scale),
        ),
        ft.Text(f"제목 {t['title_length']}자", size=fs(12, scale)),
    ]
    if report.get("calls_left") is not None:
        out.append(ft.Text(
            f"오늘 남은 네이버 API 호출: {report['calls_left']}회 (측정값은 7일간 저장되어 같은 키워드는 다시 세지 않습니다)",
            size=fs(10, scale), color=BRAND_COLORS["text_muted"],
        ))
    return out


def build(page: ft.Page, state: AppState) -> ft.Control:
    scale = state.font_scale
    title_field = ft.TextField(label="제목", hint_text="블로그에 올릴(또는 올린) 글의 제목")
    body_field = ft.TextField(
        label="본문", multiline=True, min_lines=10, max_lines=18,
        hint_text="네이버 편집기에서 본문 전체를 복사해 붙여넣으세요. 사진은 빠져도 됩니다.",
    )
    photo_field = ft.TextField(label="사진 수", value="0", width=120, keyboard_type=ft.KeyboardType.NUMBER)
    use_api = ft.Checkbox(label="네이버 API로 키워드 측정 (호출 약 10~20회, 측정값은 캐시)", value=True)
    status = ft.Text("", size=fs(12, scale))
    spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
    results = ft.Column(spacing=8)
    button = ft.FilledButton("🔍 진단하기")

    def on_audit(e: ft.Event):
        if not (title_field.value or "").strip() or not (body_field.value or "").strip():
            status.value = "제목과 본문을 모두 넣어주세요."
            status.color = "#B3261E"
            status.update()
            return
        try:
            photos = max(0, int((photo_field.value or "0").strip()))
        except ValueError:
            photos = 0

        button.disabled = True
        spinner.visible = True
        status.value = "⏳ 진단 중… (키워드 측정과 AI 판정에 20~40초 걸릴 수 있어요)"
        status.color = BRAND_COLORS["text_muted"]
        results.controls = []
        for c in (button, spinner, status, results):
            c.update()
        yield  # '진단 중' 표시를 먼저 그린다 — 모듈 docstring 참고.

        try:
            report = post_audit.audit(title_field.value.strip(), body_field.value, photos, use_api=use_api.value)
            results.controls = _render(report, scale)
            status.value = "✅ 진단 완료"
            status.color = "#1B6E3C"
        except Exception as exc:  # noqa: BLE001
            status.value = f"❌ 진단 실패: {exc}"
            status.color = "#B3261E"
        finally:
            button.disabled = False
            spinner.visible = False
        for c in (button, spinner, status, results):
            c.update()

    button.on_click = on_audit

    return ft.Column(
        [
            ft.Text("📋 글 진단", size=fs(24, scale), weight=ft.FontWeight.BOLD),
            ft.Text(
                "앱 밖에서 직접 쓴 블로그 글을 붙여넣으면, 제목의 말이 실제로 검색되는지, 이 글에 맞는 "
                "더 좋은 검색어는 무엇인지, 본문 분량·구매 정보·표시광고 주의 표현을 점검합니다. "
                "글을 고쳐 쓰지는 않습니다.",
                size=fs(12, scale), color=BRAND_COLORS["text_muted"],
            ),
            title_field,
            body_field,
            ft.Row([photo_field, use_api], wrap=True),
            ft.Row([button, spinner, status]),
            ft.Divider(),
            results,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
