# -*- coding: utf-8 -*-
"""OSMU 콘텐츠 워크벤치 — 공개 사용자 가이드 (GitHub에 커밋되는 문서).

etc/ 아래의 이전 가이드들과 달리 이 파일은 저장소에 실제로 커밋되는 문서입니다.
그래서 특정 회사(더스티치/더봄봄) 예시 대신 일반화된 설명을 쓰고, 이 코드베이스가
"clone해서 자기 회사에 맞게 쓰는 템플릿"이라는 전제를 반영합니다.

특히 사용자가 가장 헷갈려하는 ⚙️ 설정 → 네이버 API 탭의 동작 원리를 깊게 다룹니다:
API가 왜 두 개인지, 각각 무엇에 쓰이는지, 캐시·비용이 어떻게 절감되는지.
내용은 views/06_settings.py, ai_workers/keyword_research.py, ai_workers/news_search.py,
CLAUDE.md를 직접 읽고 확인한 사실만 반영했습니다.
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ---------- palette (core/brand_seed.py BRAND_COLORS — 이 앱의 기본 테마) ----------
PRIMARY      = RGBColor(0xA6, 0x22, 0x4B)
PRIMARY_DARK = RGBColor(0x7C, 0x17, 0x38)
SECONDARY    = RGBColor(0x2B, 0x4C, 0x8C)
ACCENT       = RGBColor(0xC9, 0xA2, 0x27)
MINT         = RGBColor(0x86, 0xC1, 0xB3)
BG           = RGBColor(0xFB, 0xF7, 0xF2)
TEXT         = RGBColor(0x2B, 0x21, 0x18)
TEXT_MUTED   = RGBColor(0x8A, 0x7B, 0x6B)
WHITE        = RGBColor(0xFF, 0xFF, 0xFF)
CARD_ROSE    = RGBColor(0xF3, 0xE3, 0xE8)
CARD_BLUE    = RGBColor(0xE7, 0xEC, 0xF5)
ROW_ALT      = RGBColor(0xF6, 0xF1, 0xEA)
WARN         = RGBColor(0xC0, 0x39, 0x2B)
GOOD         = RGBColor(0x2E, 0x8B, 0x57)
FREE_TAG     = RGBColor(0x2E, 0x8B, 0x57)
PAID_TAG     = RGBColor(0xC0, 0x39, 0x2B)

FONT = "맑은 고딕"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = prs.slide_width, prs.slide_height

PAGE = {"n": 0}


def next_page():
    PAGE["n"] += 1
    return PAGE["n"]


def set_bg(slide, color):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color


def add_rect(slide, x, y, w, h, color, line=False, round_=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if round_ else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(shape_type, x, y, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    if line:
        shp.line.color.rgb = line
        shp.line.width = Pt(1)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def add_text(slide, x, y, w, h, text, size=18, color=TEXT, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT,
             line_spacing=1.0, italic=False):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.alignment = align
        p.line_spacing = line_spacing
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.color.rgb = color
            r.font.bold = bold
            r.font.italic = italic
            r.font.name = font
    return tb


def add_bullets(slide, x, y, w, h, items, size=14, color=TEXT, font=FONT,
                 space_after=8, line_spacing=1.15, muted=TEXT_MUTED):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    first = True
    for level, text, bold in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        prefix = "▪ " if level == 0 else "－ "
        p.text = ("     " if level == 1 else "") + prefix + text
        p.space_after = Pt(space_after)
        p.line_spacing = line_spacing
        for r in p.runs:
            r.font.size = Pt(size if level == 0 else size - 1)
            r.font.color.rgb = color if level == 0 else muted
            r.font.bold = bold
            r.font.name = font
    return tb


def header_bar(slide, kicker, title, accent=PRIMARY):
    add_rect(slide, 0, 0, SW, Inches(1.15), PRIMARY_DARK)
    add_rect(slide, 0, Inches(1.15), SW, Pt(3), accent)
    add_text(slide, Inches(0.55), Inches(0.12), Inches(9.5), Inches(0.32), kicker,
             size=12, color=accent, bold=True)
    add_text(slide, Inches(0.5), Inches(0.38), Inches(11.5), Inches(0.68), title,
             size=24, color=WHITE, bold=True)
    add_text(slide, Inches(0.5), SH - Inches(0.4), Inches(9), Inches(0.3),
             "OSMU 콘텐츠 워크벤치 · 사용자 가이드", size=9, color=TEXT_MUTED)
    add_text(slide, SW - Inches(1.2), SH - Inches(0.4), Inches(0.8), Inches(0.3),
             f"{next_page():02d}", size=9, color=TEXT_MUTED, align=PP_ALIGN.RIGHT)


def new_slide(bg=BG):
    s = prs.slides.add_slide(BLANK)
    set_bg(s, bg)
    return s


def tag(slide, x, y, w, h, text, color):
    add_rect(slide, x, y, w, h, color, round_=True)
    add_text(slide, x, y, w, h, text, size=10.5, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def style_table(table, header_color=PRIMARY_DARK, col_widths=None, header_size=12.5, body_size=11.5):
    n_rows, n_cols = len(table.rows), len(table.columns)
    if col_widths:
        for i, w in enumerate(col_widths):
            table.columns[i].width = w
    for ci in range(n_cols):
        cell = table.cell(0, ci)
        cell.fill.solid()
        cell.fill.fore_color.rgb = header_color
        for p in cell.text_frame.paragraphs:
            p.alignment = PP_ALIGN.CENTER
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(header_size)
                r.font.color.rgb = WHITE
                r.font.name = FONT
    for ri in range(1, n_rows):
        for ci in range(n_cols):
            cell = table.cell(ri, ci)
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if ri % 2 == 1 else ROW_ALT
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.LEFT
                for r in p.runs:
                    r.font.size = Pt(body_size)
                    r.font.color.rgb = TEXT
                    r.font.name = FONT
            cell.margin_left = Pt(8)
            cell.margin_right = Pt(8)
            cell.margin_top = Pt(4)
            cell.margin_bottom = Pt(4)


def section_divider(kicker, title, sub, color=PRIMARY):
    s = new_slide(color)
    next_page()
    add_rect(s, 0, 0, Inches(0.22), SH, ACCENT)
    add_text(s, Inches(0.9), Inches(2.7), Inches(11), Inches(0.5), kicker,
              size=16, color=ACCENT, bold=True)
    add_text(s, Inches(0.85), Inches(3.15), Inches(11.5), Inches(1.2), title,
              size=34, color=WHITE, bold=True)
    add_text(s, Inches(0.9), Inches(4.25), Inches(10.5), Inches(0.9), sub,
              size=15, color=RGBColor(0xF0, 0xE3, 0xE8) if color == PRIMARY else RGBColor(0xDE, 0xE6, 0xF3),
              line_spacing=1.3)
    return s


def step_box(slide, x, y, w, h, num, title, desc, color=PRIMARY, num_w=Inches(0.55),
             title_size=13.5, desc_size=11.5):
    add_rect(slide, x, y, num_w, h, color)
    add_text(slide, x, y, num_w, h, str(num), size=19, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_rect(slide, x + num_w, y, w - num_w, h, WHITE)
    add_text(slide, x + num_w + Inches(0.2), y + Inches(0.08), w - num_w - Inches(0.4), Inches(0.35),
              title, size=title_size, color=TEXT, bold=True)
    add_text(slide, x + num_w + Inches(0.2), y + Inches(0.42), w - num_w - Inches(0.4), h - Inches(0.5),
              desc, size=desc_size, color=TEXT_MUTED, line_spacing=1.2)


# ============================================================
# Slide — Title
# ============================================================
s = new_slide(PRIMARY_DARK)
add_rect(s, 0, 0, Inches(0.22), SH, ACCENT)
add_text(s, Inches(0.9), Inches(2.1), Inches(10), Inches(0.5),
          "OSMU CONTENT WORKBENCH — USER GUIDE", size=15, color=ACCENT, bold=True)
add_text(s, Inches(0.85), Inches(2.6), Inches(11.5), Inches(1.6),
          "OSMU 콘텐츠 워크벤치\n사용자 가이드", size=38, color=WHITE, bold=True, line_spacing=1.15)
add_text(s, Inches(0.9), Inches(4.15), Inches(10.5), Inches(0.9),
          "일반 사용법 · ⚙️ 네이버 API 설정 완전정복 · 내 회사에 맞게 커스터마이징하기",
          size=17, color=RGBColor(0xF0, 0xE3, 0xE8))
add_rect(s, Inches(0.9), Inches(5.15), Inches(1.1), Pt(4), ACCENT)
add_text(s, Inches(0.9), Inches(6.5), Inches(9), Inches(0.5),
          "이 문서는 저장소에 포함되어 있으며, 누구나 clone 후 그대로 참고할 수 있습니다.",
          size=12, color=TEXT_MUTED)

# ============================================================
# Slide — 빠른 시작 (맨 처음 배치) ① 3단계 개요
# ============================================================
s = new_slide()
header_bar(s, "QUICK START", "⚡ 5분 만에 내 회사 버전 만들기")
add_text(s, Inches(0.55), Inches(1.35), Inches(12.2), Inches(0.4),
          "아래 4단계만 따라 하면, 지금 보이는 예시 브랜드를 여러분의 회사로 바꿀 수 있습니다.",
          size=13, color=TEXT_MUTED)
qs_steps = [
    ("① git clone", "터미널에서 이 저장소를 내 컴퓨터로 내려받습니다.",
     "git clone <이 저장소 GitHub 주소>\ncd <내려받은 폴더>"),
    ("② company_info/ 에 우리 회사 자료 업로드", "폴더 안의 예시 파일은 지우거나 그대로 두고, 회사소개서·SNS 정보·로고 등 "
     "우리 회사 자료를 같은 폴더에 넣습니다. (파일 형식은 자유 — PDF, txt, 이미지 등)", None),
    ("③ 터미널에서 claude 실행 후 프롬프트 입력", "다음 슬라이드의 예시 프롬프트를 그대로 복사해서 붙여넣습니다.", None),
    ("④ 결과 확인", "🧵 브랜드 킷 화면에서 값이 반영됐는지 확인하고, ⚙️ 설정에서 LLM·네이버 API 키를 직접 등록합니다.", None),
]
y = Inches(1.9)
for title, desc, code in qs_steps:
    h = Inches(1.35) if code else Inches(1.0)
    add_rect(s, Inches(0.55), y, Inches(0.65), h, PRIMARY)
    add_text(s, Inches(0.55), y, Inches(0.65), h, title[1], size=22, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_rect(s, Inches(1.2), y, Inches(11.55), h, WHITE)
    add_text(s, Inches(1.45), y + Inches(0.1), Inches(11.1), Inches(0.35), title, size=14, color=TEXT, bold=True)
    add_text(s, Inches(1.45), y + Inches(0.46), Inches(11.1), Inches(0.42), desc, size=11.5, color=TEXT_MUTED, line_spacing=1.2)
    if code:
        add_rect(s, Inches(1.45), y + Inches(0.86), Inches(10.8), Inches(0.42), PRIMARY_DARK, round_=True)
        add_text(s, Inches(1.65), y + Inches(0.86), Inches(10.4), Inches(0.42), code.split("\n")[0],
                  size=11.5, color=RGBColor(0xE8, 0xD9, 0xDE), font="Consolas", anchor=MSO_ANCHOR.MIDDLE)
    y += h + Inches(0.15)

# ============================================================
# Slide — 빠른 시작 ② 복사해서 쓰는 프롬프트 예시
# ============================================================
s = new_slide()
header_bar(s, "QUICK START", "③번 단계 — 복사해서 쓰는 프롬프트 예시")
add_text(s, Inches(0.55), Inches(1.35), Inches(12.2), Inches(0.5),
          "빈칸(【 】)만 우리 회사 정보로 채워서 Claude Code에게 그대로 붙여넣으세요.",
          size=13, color=TEXT_MUTED)
prompt_text = (
    "CLAUDE.md를 참고해서, company_info/ 폴더에 넣어둔 우리 회사 자료로\n"
    "브랜드 킷을 채워줘.\n"
    "\n"
    "회사명: 【○○○ 주식회사】\n"
    "브랜드/제품명: 【○○○】\n"
    "업종: 【예: 화장품 제조·판매】\n"
    "홈페이지: 【https://...】\n"
    "네이버 블로그 아이디 / 인스타그램 핸들: 【...】 / 【@...】\n"
    "\n"
    "컴플라이언스(표시·광고) 기준도 우리 업종에 맞게 다시 검토해서 반영해줘.\n"
    "브랜드 컬러는 로고 이미지를 참고해서 제안해줘."
)
add_rect(s, Inches(0.55), Inches(2.0), Inches(12.2), Inches(4.3), PRIMARY_DARK, round_=True)
tb = s.shapes.add_textbox(Inches(0.9), Inches(2.3), Inches(11.5), Inches(3.7))
tf = tb.text_frame
tf.word_wrap = True
for i, line in enumerate(prompt_text.split("\n")):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.text = line if line else " "
    p.line_spacing = 1.35
    for r in p.runs:
        r.font.size = Pt(14)
        r.font.color.rgb = RGBColor(0xF5, 0xE9, 0xEC)
        r.font.name = "Consolas"
add_text(s, Inches(0.55), Inches(6.5), Inches(12.2), Inches(0.6),
          "Claude Code가 자료를 읽고 값을 제안한 뒤 반영합니다 — 큰 변경(컴플라이언스 기준, 색상) 전에는 확인을 구합니다.",
          size=12, color=TEXT_MUTED, italic=True)

# ============================================================
# Slide — 목차
# ============================================================
s = new_slide()
header_bar(s, "CONTENTS", "목차")
parts = [
    ("QUICK START", "⚡ 5분 만에 내 회사 버전 만들기", "git clone부터 프롬프트 한 번으로 커스터마이징까지", ACCENT),
    ("PART 1", "일반 사용법", "콘텐츠를 만들고 게시하기까지의 기본 흐름", PRIMARY),
    ("PART 2", "설정 완전정복 — 네이버 API", "가장 헷갈리는 부분: API가 왜 2개이고, 무엇에 쓰이며, 어떻게 동작하는지", PRIMARY),
    ("PART 3", "내 회사에 맞게 커스터마이징", "이 저장소는 템플릿입니다 — Claude Code로 내 브랜드에 맞추는 법", SECONDARY),
    ("PART 4", "부록 · FAQ", "자주 겪는 문제와 해결", SECONDARY),
]
y = Inches(1.4)
for tag_txt, title, desc, color in parts:
    add_rect(s, Inches(0.55), y, Inches(12.2), Inches(1.02), WHITE)
    add_rect(s, Inches(0.55), y, Inches(0.12), Inches(1.02), color)
    tag(s, Inches(0.85), y + Inches(0.28), Inches(1.75), Inches(0.44), tag_txt, color)
    add_text(s, Inches(2.85), y + Inches(0.08), Inches(9.6), Inches(0.4), title, size=16.5, color=TEXT, bold=True)
    add_text(s, Inches(2.85), y + Inches(0.52), Inches(9.6), Inches(0.45), desc, size=11.5, color=TEXT_MUTED)
    y += Inches(1.14)

# ============================================================
# PART 1 divider
# ============================================================
section_divider("PART 1", "일반 사용법", "콘텐츠 작성부터 게시까지, 화면 흐름 그대로 따라갑니다", PRIMARY)

# ---- 이 도구는 무엇을 하나 ----
s = new_slide()
header_bar(s, "PART 1 · 일반 사용법", "이 도구는 무엇을 하나요")
boxes = [
    ("① 한 번 쓰고 네 채널로", "담당자 메모 + 사진만 넣으면 네이버 블로그·인스타 캡션·X 스레드·쇼츠 자막을 AI가 한 번에 생성합니다.", PRIMARY),
    ("② 컴플라이언스 자동 검수", "업종에 맞는 표시·광고 규제 기준으로 모든 초안을 금기어 치환 + AI 법무 검토로 검수합니다.", SECONDARY),
    ("③ 내 컴퓨터에만 저장", "클라우드 서버 없이 로컬 SQLite에 저장됩니다. API 키는 암호화되고, 데이터는 이 컴퓨터를 떠나지 않습니다.", MINT),
]
x = Inches(0.55)
for title, desc, color in boxes:
    add_rect(s, x, Inches(1.7), Inches(3.95), Inches(3.1), CARD_ROSE if color != MINT else CARD_BLUE)
    add_rect(s, x, Inches(1.7), Inches(3.95), Inches(0.08), color)
    add_text(s, x + Inches(0.25), Inches(2.0), Inches(3.45), Inches(0.55), title, size=15, color=TEXT, bold=True)
    add_text(s, x + Inches(0.25), Inches(2.6), Inches(3.5), Inches(2.0), desc, size=12.5, color=TEXT, line_spacing=1.3)
    x += Inches(4.2)
add_text(s, Inches(0.55), Inches(5.1), Inches(12.2), Inches(1.8),
          "이 저장소를 clone하면 예시로 구성된 브랜드 데이터(가상의 회사)가 이미 채워져 있습니다. "
          "Part 3에서 이를 여러분의 회사 정보로 바꾸는 방법을 다룹니다.",
          size=13, color=TEXT_MUTED, italic=True, line_spacing=1.3)

# ---- 화면 구성 & 흐름 ----
s = new_slide()
header_bar(s, "PART 1 · 일반 사용법", "화면 구성과 콘텐츠 흐름")
rows = [
    ("화면", "하는 일"),
    ("📊 대시보드", "상태별 칸반, 저장소 사용량, 절약된 토큰, 콘텐츠 초기화"),
    ("✍️ 워크벤치", "메모·사진·본문 편집(좌) + 4채널 시뮬레이터(우), 초안 생성·수정"),
    ("📰 뉴스 큐레이션", "브랜드 키워드로 뉴스 검색 → 워크벤치로 전달 (무료, API 키 불필요)"),
    ("🧵 브랜드 킷", "페르소나·톤앤매너·핵심 팩트·용어집·SEO 키워드·금기어 사전"),
    ("🚀 네이버 게시", "반자동 게시(로그인 세션) · 수동 완료 처리 · 채널별 콘텐츠 조회"),
    ("⚙️ 설정 · 토큰", "LLM 벤더, 네이버 API 키 2종, 키워드 갱신, 사용량 — Part 2에서 상세히"),
]
tbl = s.shapes.add_table(len(rows), 2, Inches(0.55), Inches(1.55), Inches(12.2), Inches(3.9)).table
for r, row in enumerate(rows):
    for c, val in enumerate(row):
        tbl.cell(r, c).text = val
style_table(tbl, col_widths=[Inches(3.2), Inches(9.0)])
stages = ["소재 대기", "생성 중", "초안 완료", "게시 대기", "게시 완료"]
x = Inches(0.55)
w = Inches(2.05)
gap = Inches(0.35)
for i, st_ in enumerate(stages):
    color = PRIMARY if i < 3 else SECONDARY
    add_rect(s, x, Inches(5.75), w, Inches(0.85), color, round_=True)
    add_text(s, x, Inches(5.75), w, Inches(0.85), st_, size=12.5, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    if i < len(stages) - 1:
        add_text(s, x + w, Inches(5.75), gap, Inches(0.85), "→", size=18, color=TEXT_MUTED,
                  align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    x += w + gap

# ---- 워크벤치 ----
s = new_slide()
header_bar(s, "PART 1 · 일반 사용법", "워크벤치 — 새 콘텐츠 만들기")
steps = [
    ("➕ 새 콘텐츠", "빈 초안이 만들어집니다. 이미 아무것도 쓰지 않은 빈 초안이 있으면 새로 만들지 않고 재사용합니다."),
    ("담당자 메모 작성", "'초안의 씨앗'입니다. 현장에서 있었던 일, 반응이 좋았던 포인트 등을 자유롭게 적습니다."),
    ("공지·제품 정보 입력 (해당 시)", "날짜·가격·주문 방법처럼 AI가 지어내면 안 되는 정보는 별도 입력창에 채웁니다."),
    ("사진 첨부 → 🪄 초안 생성", "네이버 본문·인스타 캡션·X 스레드·쇼츠 자막이 한 번에 생성됩니다."),
]
y = Inches(1.5)
for i, (t, d) in enumerate(steps, start=1):
    step_box(s, Inches(0.55), y, Inches(12.2), Inches(1.15), i, t, d)
    y += Inches(1.25)
add_text(s, Inches(0.55), Inches(6.5), Inches(12.2), Inches(0.5),
          "생성 후 4개 탭(네이버/인스타/X/쇼츠)에서 직접 편집·복사할 수 있고, 우측 시뮬레이터로 실제 레이아웃을 미리 봅니다.",
          size=12, color=TEXT_MUTED)

# ---- 뉴스 큐레이션 + 네이버 게시 ----
s = new_slide()
header_bar(s, "PART 1 · 일반 사용법", "뉴스 큐레이션 & 네이버 게시")
add_text(s, Inches(0.55), Inches(1.4), Inches(5.9), Inches(0.4), "📰 뉴스 큐레이션", size=15, color=PRIMARY, bold=True)
add_bullets(s, Inches(0.55), Inches(1.85), Inches(5.9), Inches(2.8), [
    (0, "브랜드 키워드로 관련 기사를 찾아 워크벤치로 전달합니다.", False),
    (0, "Google 뉴스 RSS + 네이버 뉴스 검색 결과 페이지를 직접 조회합니다 — API 키가 필요 없고 완전 무료입니다.", True),
    (0, "뉴스 기반 글은 편집 방침상 본문 끝에 원문 링크가 자동으로 붙습니다.", False),
], size=12.5, space_after=10, line_spacing=1.25)

add_text(s, Inches(6.85), Inches(1.4), Inches(5.9), Inches(0.4), "🚀 네이버 게시", size=15, color=PRIMARY, bold=True)
add_bullets(s, Inches(6.85), Inches(1.85), Inches(5.9), Inches(2.8), [
    (0, "네이버가 봇 게시를 막기 때문에 마지막 [발행]은 항상 사람이 직접 누릅니다.", False),
    (0, "반자동: 로그인 세션 저장 후 [지금 게시] → Chrome 창이 자동 입력 → 직접 [발행] 클릭.", False),
    (0, "수동: 직접 복사해 붙여넣었다면 [✅ 수동으로 완료]로 목록 정리.", False),
], size=12.5, space_after=10, line_spacing=1.25)
add_rect(s, Inches(0.55), Inches(5.0), Inches(12.2), Inches(1.5), CARD_ROSE)
add_text(s, Inches(0.85), Inches(5.2), Inches(11.6), Inches(0.4), "게시 완료 후에도", size=13.5, color=TEXT, bold=True)
add_text(s, Inches(0.85), Inches(5.6), Inches(11.6), Inches(0.75),
          "인스타/X/쇼츠는 자동 게시가 없어 담당자가 직접 올려야 합니다. 네이버 게시 완료 탭의 "
          "[📄 콘텐츠 보기 (전체 채널)]에서 언제든 다시 조회·복사할 수 있습니다.",
          size=12, color=TEXT, line_spacing=1.25)

# ============================================================
# PART 2 divider — 네이버 API
# ============================================================
section_divider("PART 2", "설정 완전정복 — 네이버 API",
                "가장 많이 헷갈리는 부분입니다. 왜 API가 2개인지, 각각 무엇에 쓰이는지 처음부터 설명합니다.",
                SECONDARY)

# ---- 왜 API 키가 필요한가 (큰 그림) ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "먼저, 이 앱이 네이버와 왜 통신하나요", accent=SECONDARY)
add_text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.5),
          "이 앱에서 '네이버'와 관련된 기능은 셋입니다 — 이 중 API 키가 필요한 것은 하나뿐입니다.",
          size=13.5, color=TEXT_MUTED)
rows3 = [
    ("기능", "무엇을 하나", "API 키 필요?"),
    ("📰 뉴스 큐레이션", "네이버 뉴스 검색 결과 페이지를 직접 조회 (스크래핑)", "❌ 불필요 · 완전 무료"),
    ("🚀 네이버 게시", "로그인 세션으로 브라우저를 자동 조작해 글을 입력 (Playwright)", "❌ 불필요 · 세션 쿠키만 사용"),
    ("🔑 키워드 조사·경쟁도 분석", "네이버가 공식 제공하는 데이터 API로 검색량·경쟁 문서 수를 조회", "✅ 필요 · 이번 파트의 주제"),
]
tbl = s.shapes.add_table(len(rows3), 3, Inches(0.55), Inches(2.0), Inches(12.2), Inches(2.6)).table
for r, row in enumerate(rows3):
    for c, val in enumerate(row):
        tbl.cell(r, c).text = val
style_table(tbl, header_color=SECONDARY, col_widths=[Inches(3.2), Inches(6.2), Inches(2.8)])
add_rect(s, Inches(0.55), Inches(4.9), Inches(12.2), Inches(1.3), CARD_BLUE)
add_text(s, Inches(0.85), Inches(5.05), Inches(11.6), Inches(0.4), "즉, 이 API 키를 등록하지 않아도 앱의 핵심 기능(콘텐츠 생성·게시)은 그대로 동작합니다.", size=13, color=SECONDARY, bold=True)
add_text(s, Inches(0.85), Inches(5.45), Inches(11.6), Inches(0.6),
          "다만 '어떤 키워드로 써야 검색에 잘 걸릴지'를 데이터 기반으로 판단하려면 이 API가 필요합니다 — 없으면 감(brand_kit에 수동으로 적어둔 키워드)에 의존하게 됩니다.",
          size=11.5, color=TEXT, line_spacing=1.25)

# ---- 두 개의 API 비교 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "두 개의 서로 다른 네이버 API", accent=SECONDARY)
add_text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.5),
          "이름이 둘 다 '네이버'라 헷갈리지만, 발급처·인증 방식·비용·용도가 전부 다른 별개의 서비스입니다.",
          size=13, color=TEXT_MUTED)
colw = Inches(5.95)
add_rect(s, Inches(0.55), Inches(2.0), colw, Inches(4.3), CARD_ROSE)
add_rect(s, Inches(0.55), Inches(2.0), colw, Inches(0.08), PRIMARY)
tag(s, Inches(0.8), Inches(2.2), Inches(1.6), Inches(0.4), "유료 종량제", PAID_TAG)
add_text(s, Inches(0.8), Inches(2.7), colw - Inches(0.5), Inches(0.4), "① NAVER API HUB", size=16, color=TEXT, bold=True)
add_bullets(s, Inches(0.8), Inches(3.15), colw - Inches(0.5), Inches(3.0), [
    (0, "발급처: console.ncloud.com (네이버 클라우드 플랫폼)", False),
    (0, "인증: Client ID / Client Secret", False),
    (0, "용도: 블로그·뉴스·카페 검색 + 검색어트렌드(Data Lab)", True),
    (0, "이 앱에서: 특정 키워드로 이미 올라온 블로그 글이 몇 개인지 세어 '경쟁도'를 계산", False),
], size=11.5, space_after=10, line_spacing=1.25)

add_rect(s, Inches(6.85), Inches(2.0), colw, Inches(4.3), CARD_BLUE)
add_rect(s, Inches(6.85), Inches(2.0), colw, Inches(0.08), SECONDARY)
tag(s, Inches(7.1), Inches(2.2), Inches(1.6), Inches(0.4), "완전 무료", FREE_TAG)
add_text(s, Inches(7.1), Inches(2.7), colw - Inches(0.5), Inches(0.4), "② 검색광고 API", size=16, color=TEXT, bold=True)
add_bullets(s, Inches(7.1), Inches(3.15), colw - Inches(0.5), Inches(3.0), [
    (0, "발급처: searchad.naver.com (네이버 검색광고 광고주센터)", False),
    (0, "인증: CUSTOMER_ID / 액세스라이선스 / 비밀키", False),
    (0, "용도: 정확한 월간 검색량 + 연관키워드 대량 발굴", True),
    (0, "이 앱에서: 후보 키워드를 찾고, 각각 한 달에 몇 번 검색되는지 조회", False),
], size=11.5, space_after=10, line_spacing=1.25)

# ---- NAVER API HUB 발급 절차 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "① NAVER API HUB — 발급 절차", accent=SECONDARY)
steps3 = [
    "console.ncloud.com 로그인 (네이버 클라우드 플랫폼 계정 — 없으면 새로 가입)",
    "Menu > All Services > Application Services > NAVER API HUB > Subscription → 약관 동의 (결제수단 등록 필요)",
    "Application > [Application 등록] — API 카테고리에서 검색(블로그·뉴스·카페)과 Data Lab을 모두 선택",
    "목록에서 [인증 정보] → X-NCP-APIGW-API-KEY-ID(Client ID), X-NCP-APIGW-API-KEY(Client Secret) 복사",
    "[한도 및 알림]에서 일·월 상한과 70%/90% 알림 설정 (권장)",
]
y = Inches(1.6)
for i, txt in enumerate(steps3, start=1):
    add_rect(s, Inches(0.55), y, Inches(0.5), Inches(0.5), SECONDARY, round_=True)
    add_text(s, Inches(0.55), y, Inches(0.5), Inches(0.5), str(i), size=16, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_text(s, Inches(1.25), y + Inches(0.02), Inches(11.5), Inches(0.45), txt, size=12.5, color=TEXT, line_spacing=1.15)
    y += Inches(0.6)
add_rect(s, Inches(0.55), Inches(4.75), Inches(12.2), Inches(0.8), CARD_BLUE)
add_text(s, Inches(0.85), Inches(4.88), Inches(11.6), Inches(0.55),
          "⚠️ developers.naver.com의 키는 여기서 동작하지 않습니다 (인증 헤더 이름이 다름). 반드시 위 절차로 새로 발급받아야 합니다.",
          size=12, color=WARN, bold=True, line_spacing=1.2)
add_rect(s, Inches(0.55), Inches(5.7), Inches(12.2), Inches(1.0), WHITE)
add_text(s, Inches(0.85), Inches(5.83), Inches(11.6), Inches(0.8),
          "💡 \"요청한 API가 이 Application에서 활성화되어 있지 않습니다\" 오류 → 구독은 됐지만 3번의 API 선택이 빠진 것입니다. "
          "콘솔에서 해당 Application의 햄버거 버튼 > [Application 수정]에서 검색·Data Lab을 체크하세요.",
          size=11.5, color=TEXT, line_spacing=1.25)

# ---- NAVER API HUB 동작 방식 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "① NAVER API HUB — 실제로 어떻게 쓰이나", accent=SECONDARY)
add_text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.5),
          "이 키는 '경쟁도 조사' 한 곳에만 쓰입니다 — 특정 키워드로 검색했을 때 블로그 문서가 몇 개나 이미 있는지 셉니다.",
          size=13, color=TEXT_MUTED)
add_bullets(s, Inches(0.55), Inches(2.1), Inches(12.2), Inches(2.3), [
    (0, "문서 수가 많을수록 그 키워드로 쓴 글이 검색 상위에 오르기 어렵다는 뜻 — 그래서 '경쟁도'입니다.", False),
    (0, "호출 1번 = 키워드 1개 조사. 유료 종량제라, ⚙️ 설정 화면에 등록한 일일 호출 상한(기본 500회)을 이 앱이 스스로 방어선으로 지킵니다 — 상한 도달 시 호출 자체를 보내지 않고 차단합니다.", True),
    (0, "네이버 콘솔의 [한도 및 알림]은 별개의 안전장치이니 함께 걸어두는 것을 권장합니다.", False),
], size=13, space_after=12, line_spacing=1.3)
add_rect(s, Inches(0.55), Inches(4.7), Inches(12.2), Inches(2.0), CARD_BLUE)
add_text(s, Inches(0.85), Inches(4.85), Inches(11.6), Inches(0.4), "🔁 한 번 조사한 키워드는 30일간 캐시됩니다", size=14, color=SECONDARY, bold=True)
add_text(s, Inches(0.85), Inches(5.3), Inches(11.6), Inches(1.2),
          "같은 키워드를 다시 조회해도 캐시가 살아있는 동안은 호출이 나가지 않아 비용이 들지 않습니다. "
          "이 유효기간을 지나면(DOCUMENT_CACHE_DAYS = 30일) '경쟁도 측정이 만료'되었다는 경고가 뜨고, "
          "만료된 키워드는 전환 가중치 없이 순위가 매겨집니다 — ⚙️ 설정에서 [🔄 숫자만 새로 재기]로 갱신하세요.",
          size=12, color=TEXT, line_spacing=1.3)

# ---- 검색광고 API 발급 절차 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "② 검색광고 API — 발급 절차", accent=SECONDARY)
steps4 = [
    "searchad.naver.com 회원가입 — 개인 광고주·개인사업자·법인 모두 가능, 광고 집행 불필요",
    "로그인 후 우측 상단 [광고시스템] 클릭 → manage.searchad.naver.com으로 진입 (메인 화면엔 '도구' 탭이 없음)",
    "상단 도구 > API 사용 관리",
    "[네이버 검색광고 API 서비스 신청] 버튼 클릭 — 심사·승인 없이 약관 동의만으로 즉시 발급",
    "CUSTOMER_ID(7자리 숫자) · 액세스라이선스(01000000...) · 비밀키(AQAAAA...) 세 값 복사",
]
y = Inches(1.6)
for i, txt in enumerate(steps4, start=1):
    add_rect(s, Inches(0.55), y, Inches(0.5), Inches(0.5), SECONDARY, round_=True)
    add_text(s, Inches(0.55), y, Inches(0.5), Inches(0.5), str(i), size=16, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_text(s, Inches(1.25), y + Inches(0.02), Inches(11.5), Inches(0.45), txt, size=12.5, color=TEXT, line_spacing=1.15)
    y += Inches(0.6)
add_rect(s, Inches(0.55), Inches(4.75), Inches(12.2), Inches(0.8), CARD_BLUE)
add_text(s, Inches(0.85), Inches(4.88), Inches(11.6), Inches(0.55),
          "💡 계정 책임자(마스터) 계정으로 로그인해야 보입니다. 운영관리 권한만 위임받은 계정에서는 키가 표시되지 않습니다.",
          size=12, color=SECONDARY, bold=True, line_spacing=1.2)
add_rect(s, Inches(0.55), Inches(5.7), Inches(12.2), Inches(0.75), WHITE)
add_text(s, Inches(0.85), Inches(5.82), Inches(11.6), Inches(0.55),
          "💡 3번에 들어갔는데 키가 안 보인다면 아직 서비스 신청 전입니다 — 4번의 신청 버튼을 먼저 눌러야 합니다.",
          size=11.5, color=TEXT, line_spacing=1.2)

# ---- 검색광고 API 동작 방식 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "② 검색광고 API — 실제로 어떻게 쓰이나", accent=SECONDARY)
add_text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.5),
          "이 키는 '얼마나 검색되는가'를 알려주는 두 가지 조회에 쓰입니다.",
          size=13, color=TEXT_MUTED)
colw = Inches(5.95)
add_rect(s, Inches(0.55), Inches(2.0), colw, Inches(3.4), WHITE)
add_text(s, Inches(0.8), Inches(2.2), colw - Inches(0.5), Inches(0.4), "정확한 월간 검색량", size=14, color=SECONDARY, bold=True)
add_text(s, Inches(0.8), Inches(2.65), colw - Inches(0.5), Inches(2.5),
          "특정 키워드가 PC·모바일에서 한 달에 몇 번 검색되는지 정확한 숫자를 알려줍니다. "
          "네이버는 10회 미만은 전부 '10'으로 뭉뚱그려 반환합니다 — 그 이하는 '적다'가 아니라 사실상 "
          "측정 불가로 취급합니다.",
          size=12, color=TEXT, line_spacing=1.3)
add_rect(s, Inches(6.85), Inches(2.0), colw, Inches(3.4), WHITE)
add_text(s, Inches(7.1), Inches(2.2), colw - Inches(0.5), Inches(0.4), "연관키워드 발굴", size=14, color=SECONDARY, bold=True)
add_text(s, Inches(7.1), Inches(2.65), colw - Inches(0.5), Inches(2.5),
          "씨앗 키워드 하나를 주면 네이버 광고주들이 실제로 입찰하는 관련 검색어를 대량으로 돌려줍니다. "
          "⚙️ 설정의 [🔎 후보 찾기] 버튼이 이 기능을 씁니다 — 이 단계는 무료라 몇 번을 눌러도 비용이 들지 않습니다.",
          size=12, color=TEXT, line_spacing=1.3)
add_rect(s, Inches(0.55), Inches(5.6), Inches(12.2), Inches(1.1), CARD_BLUE)
add_text(s, Inches(0.85), Inches(5.75), Inches(11.6), Inches(0.8),
          "이 API는 완전 무료이고 위 ①의 일일 상한과도 무관합니다 — 검색량 조회·연관키워드 발굴은 "
          "몇 번을 눌러도 과금되지 않습니다. 비용이 발생하는 지점은 오직 ①(경쟁도 조사)뿐입니다.",
          size=12.5, color=SECONDARY, bold=True, line_spacing=1.3)

# ---- 두 API가 함께 도는 곳: 키워드 갱신 파이프라인 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "두 API가 함께 도는 곳 — 키워드 갱신", accent=SECONDARY)
add_text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.4),
          "⚙️ 설정 → 키워드 갱신의 4단계 각각이 어느 API를 쓰는지 표시했습니다.", size=13, color=TEXT_MUTED)
steps5 = [
    ("1. 후보 찾기", FREE_TAG, "검색광고 API", "씨앗 키워드로 연관검색어를 대량 발굴, 검색량 구간으로 필터링"),
    ("2. 경쟁도 조사", PAID_TAG, "NAVER API HUB", "후보마다 블로그 문서 수를 세어 '얼마나 치열한지' 측정 — 유일하게 비용이 드는 단계"),
    ("3. 브랜드 판정", TEXT_MUTED, "API 미사용 (LLM)", "이 브랜드에 맞는 키워드인지 AI가 판정 — 네이버 API를 부르지 않음"),
    ("4. 적용", PAID_TAG, "NAVER API HUB", "승인 시 브랜드 킷에 반영 + 최종 측정까지 완료"),
]
y = Inches(2.0)
for t, tagcolor, tagtxt, d in steps5:
    add_rect(s, Inches(0.55), y, Inches(12.2), Inches(1.0), WHITE)
    add_rect(s, Inches(0.55), y, Inches(0.12), Inches(1.0), SECONDARY)
    add_text(s, Inches(0.9), y + Inches(0.1), Inches(3.0), Inches(0.35), t, size=13.5, color=TEXT, bold=True)
    tag(s, Inches(4.1), y + Inches(0.28), Inches(2.1), Inches(0.42), tagtxt, tagcolor)
    add_text(s, Inches(6.5), y + Inches(0.14), Inches(5.9), Inches(0.7), d, size=11.5, color=TEXT_MUTED, line_spacing=1.2)
    y += Inches(1.08)
add_text(s, Inches(0.55), Inches(6.3), Inches(12.2), Inches(0.5),
          "평소 달에는 [🔄 숫자만 새로 재기] 하나로 충분 — 만료된 키워드만 다시 측정하고 나머지는 캐시(호출 0)를 씁니다.",
          size=12, color=TEXT_MUTED, italic=True)

# ---- 캐시와 비용 절감 원리 ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "캐시와 비용 절감 원리", accent=SECONDARY)
add_bullets(s, Inches(0.55), Inches(1.5), Inches(12.2), Inches(2.6), [
    (0, "조회한 모든 키워드 지표는 DB의 keyword_cache 테이블에 30일간 저장됩니다.", False),
    (0, "같은 키워드를 다시 조사하면 캐시가 살아있는 동안은 호출 없이 저장된 값을 그대로 돌려줍니다.", False),
    (0, "모든 실제 호출은 naver_api_calls 테이블에 기록되어, 일일 상한을 넘기 전에 앱이 스스로 요청을 차단합니다 — 청구서를 받고서야 아는 방식이 아닙니다.", True),
    (0, "⚙️ 설정 → 네이버 API 탭에서 오늘 호출 수·남은 호출·캐시된 키워드 수를 실시간으로 확인할 수 있습니다.", False),
], size=13.5, space_after=14, line_spacing=1.3)
add_rect(s, Inches(0.55), Inches(4.5), Inches(12.2), Inches(2.2), CARD_ROSE)
add_text(s, Inches(0.85), Inches(4.68), Inches(11.6), Inches(0.4), "⚠️ 만료는 조용히 일어납니다", size=14, color=WARN, bold=True)
add_text(s, Inches(0.85), Inches(5.12), Inches(11.6), Inches(1.4),
          "캐시가 만료되면 에러가 나거나 앱이 멈추지 않습니다 — 대신 '전환 가중치'(이 키워드로 온 방문자가 "
          "얼마나 가치 있는지)가 조용히 꺼지고, 단순히 '초안에 몇 번 등장했는지'로만 키워드 순위가 매겨집니다. "
          "브랜드 킷과 설정 화면에 경고 문구가 뜨니, 보이면 [🔄 숫자만 새로 재기]를 눌러주세요.",
          size=12, color=TEXT, line_spacing=1.35)

# ---- Naver API 전용 FAQ ----
s = new_slide()
header_bar(s, "PART 2 · 네이버 API", "자주 겪는 문제", accent=SECONDARY)
faqs = [
    ("\"요청한 API가 이 Application에서 활성화되어 있지 않습니다\"", "NAVER API HUB 콘솔 → 해당 Application > [Application 수정]에서 검색·Data Lab을 체크하세요."),
    ("검색광고 API 사용 관리에 키가 안 보임", "계정 책임자(마스터) 계정으로 로그인했는지, 서비스 신청(4단계)을 먼저 완료했는지 확인하세요."),
    ("'경쟁도 측정이 만료' 경고가 뜸", "⚙️ 설정 → 키워드 갱신 → [🔄 숫자만 새로 재기]를 누르세요. 캐시가 살아있는 값은 재과금되지 않습니다."),
    ("오늘 호출 상한을 넘었다는 메시지", "정상 동작입니다 — 청구 방지를 위한 방어선입니다. 다음 날 자동으로 초기화되거나, ⚙️ 설정에서 일일 상한을 올릴 수 있습니다."),
    ("developers.naver.com에서 발급받은 키를 넣었는데 실패", "그 키는 이 앱과 호환되지 않습니다 (인증 헤더가 다른 별개 서비스). console.ncloud.com에서 새로 발급하세요."),
]
y = Inches(1.55)
for q, a in faqs:
    add_rect(s, Inches(0.55), y, Inches(12.2), Inches(1.0), WHITE)
    add_text(s, Inches(0.8), y + Inches(0.08), Inches(11.6), Inches(0.4), f"Q. {q}", size=13, color=PRIMARY_DARK, bold=True)
    add_text(s, Inches(0.8), y + Inches(0.48), Inches(11.6), Inches(0.45), f"A. {a}", size=11.5, color=TEXT, line_spacing=1.2)
    y += Inches(1.08)

# ============================================================
# PART 3 divider — 커스터마이징
# ============================================================
section_divider("PART 3", "내 회사에 맞게 커스터마이징",
                "이 저장소는 템플릿입니다 — 지금 보이는 예시 브랜드 데이터를 여러분의 회사 정보로 바꿀 수 있습니다",
                SECONDARY)

# ---- 이 저장소는 템플릿입니다 ----
s = new_slide()
header_bar(s, "PART 3 · 커스터마이징", "이 저장소는 템플릿입니다", accent=SECONDARY)
add_bullets(s, Inches(0.55), Inches(1.5), Inches(12.2), Inches(2.2), [
    (0, "clone 직후에는 예시 브랜드(가상의 한 회사)로 이미 세팅되어 있습니다 — core/brand_seed.py 한 파일에 그 값들이 모여 있습니다.", False),
    (0, "이 예시는 앱이 실제로 어떻게 동작해야 하는지 보여주는 참고 구현일 뿐, 여러분의 회사와는 무관합니다.", False),
    (0, "코드 구조(파이프라인, DB 스키마, 화면 로직)는 그대로 두고, 회사 관련 '값'만 교체하면 됩니다.", True),
], size=13.5, space_after=14, line_spacing=1.3)
add_rect(s, Inches(0.55), Inches(4.0), Inches(12.2), Inches(2.6), CARD_BLUE)
add_text(s, Inches(0.85), Inches(4.2), Inches(11.6), Inches(0.4), "📄 CLAUDE.md", size=15, color=SECONDARY, bold=True)
add_text(s, Inches(0.85), Inches(4.65), Inches(11.6), Inches(1.8),
          "저장소 루트의 CLAUDE.md 파일이 이 커스터마이징 절차 전체를 담고 있습니다. "
          "Claude Code(또는 다른 AI 코딩 에이전트)가 이 파일을 자동으로 읽고 그대로 따라갈 수 있도록 "
          "어떤 파일의 어떤 값을 바꿔야 하는지 정확한 파일 경로와 함께 정리해 두었습니다.",
          size=13, color=TEXT, line_spacing=1.4)

# ---- Claude Code로 커스터마이징하는 법 ----
s = new_slide()
header_bar(s, "PART 3 · 커스터마이징", "Claude Code로 커스터마이징하는 법", accent=SECONDARY)
items = [
    ("① 저장소 clone", "git clone <이 저장소 주소> 로 내려받고, 안내에 따라 의존성을 설치합니다."),
    ("② 회사 자료 준비", "회사소개서, 웹사이트, SNS 정보, 로고 이미지 등을 폴더에 모으거나 Claude Code와의 대화로 전달할 준비를 합니다."),
    ("③ Claude Code에게 요청", "\"CLAUDE.md를 참고해서 내 회사에 맞게 세팅해줘\" 라고 요청합니다. 회사 자료를 함께 전달하세요."),
    ("④ 결과 확인", "🧵 브랜드 킷 화면에서 값이 잘 반영됐는지 확인하고, ⚙️ 설정에서 API 키를 직접 등록합니다."),
]
y = Inches(1.6)
for i, (t, d) in enumerate(items, start=1):
    add_rect(s, Inches(0.55), y, Inches(0.65), Inches(1.15), SECONDARY)
    add_text(s, Inches(0.55), y, Inches(0.65), Inches(1.15), str(i), size=22, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_rect(s, Inches(1.2), y, Inches(11.55), Inches(1.15), WHITE)
    add_text(s, Inches(1.45), y + Inches(0.1), Inches(11.1), Inches(0.4), t, size=14.5, color=TEXT, bold=True)
    add_text(s, Inches(1.45), y + Inches(0.55), Inches(11.1), Inches(0.55), d, size=12, color=TEXT_MUTED, line_spacing=1.25)
    y += Inches(1.28)

# ---- 무엇이 바뀌나 ----
s = new_slide()
header_bar(s, "PART 3 · 커스터마이징", "무엇이 바뀌나 — 필수 3가지", accent=SECONDARY)
must = [
    ("① 브랜드 정체성·목소리·SEO", "회사명·페르소나·핵심 팩트·용어집·SEO 키워드 — core/brand_seed.py", PRIMARY),
    ("② 컴플라이언스 기준", "업종에 맞는 표시·광고 법 조항으로 검수 기준 교체 — ai_workers/guardrail.py", SECONDARY),
    ("③ 브랜드 컬러", "core/brand_seed.py 의 BRAND_COLORS 한 곳", ACCENT),
]
x = Inches(0.55)
for title, desc, color in must:
    add_rect(s, x, Inches(1.7), Inches(3.95), Inches(2.6), CARD_ROSE if color != ACCENT else CARD_BLUE)
    add_rect(s, x, Inches(1.7), Inches(3.95), Inches(0.08), color)
    add_text(s, x + Inches(0.25), Inches(2.0), Inches(3.45), Inches(0.7), title, size=14.5, color=TEXT, bold=True, line_spacing=1.15)
    add_text(s, x + Inches(0.25), Inches(2.8), Inches(3.5), Inches(1.4), desc, size=11.5, color=TEXT_MUTED, line_spacing=1.3)
    x += Inches(4.2)
add_rect(s, Inches(0.55), Inches(4.6), Inches(12.2), Inches(2.1), WHITE)
add_text(s, Inches(0.85), Inches(4.78), Inches(11.6), Inches(0.4), "선택 사항", size=14, color=TEXT, bold=True)
add_bullets(s, Inches(0.85), Inches(5.2), Inches(11.6), Inches(1.4), [
    (0, "화면 예시 문구, 시뮬레이터 기본 표시 이름, 공지/제품 입력 필드 구성 — 업종이 많이 다를 때만", False),
    (0, "SEO 임계값(사업 축 개수, 경쟁 문서 상한, 최대 검색량) — 사업 규모에 맞게", False),
], size=12.5, space_after=8)

# ---- 주의할 점 ----
s = new_slide()
header_bar(s, "PART 3 · 커스터마이징", "주의할 점", accent=SECONDARY)
warn_items = [
    ("API 키는 화면에서 직접", "LLM·네이버 API 키는 Claude Code가 파일에 적어 넣지 않습니다. ⚙️ 설정 화면에서 사용자가 직접 입력해야 암호화되어 저장됩니다."),
    ("자료에 없는 사실은 금지", "core_facts·가격·인증 건수 등은 실제 회사 자료에서만 뽑아야 합니다. 이 값들의 충실도가 이후 SEO·컴플라이언스 판정 품질을 그대로 좌우합니다."),
    ("data/ 폴더 재시딩 함정", "앱을 이미 한 번 실행했다면 브랜드 킷이 예시 데이터로 채워진 상태입니다. core/brand_seed.py만 고쳐서는 반영되지 않으니, data/ 폴더를 삭제하고 재시작하거나 스크립트로 직접 반영해야 합니다."),
    ("컴플라이언스 기준은 법무 검토 권장", "Claude Code가 초안을 작성해도, 실제 서비스 전에는 사람이 한 번 더 확인하는 것이 안전합니다."),
]
y = Inches(1.55)
for t, d in warn_items:
    add_rect(s, Inches(0.55), y, Inches(12.2), Inches(1.15), WHITE)
    add_rect(s, Inches(0.55), y, Inches(0.12), Inches(1.15), WARN)
    add_text(s, Inches(0.9), y + Inches(0.1), Inches(11.6), Inches(0.4), t, size=13.5, color=TEXT, bold=True)
    add_text(s, Inches(0.9), y + Inches(0.52), Inches(11.6), Inches(0.55), d, size=11.5, color=TEXT_MUTED, line_spacing=1.25)
    y += Inches(1.25)

# ============================================================
# PART 4 — 부록
# ============================================================
section_divider("PART 4", "부록 · FAQ", "일반 사용 중 자주 나오는 질문", PRIMARY)

s = new_slide()
header_bar(s, "PART 4 · 부록", "일반 FAQ")
faqs2 = [
    ("기본 생성 모델이 지정되지 않았다는 오류", "⚙️ 설정 → LLM 벤더 카드에서 [연결 테스트 · 저장]을 눌러 그 벤더를 기본으로 지정하세요. 연결 테스트 성공 시 자동으로 지정됩니다."),
    ("게시 완료인데 인스타/X 내용이 안 보임", "🚀 네이버 게시 → 게시 완료 탭 → 해당 카드의 [📄 콘텐츠 보기 (전체 채널)]를 펼치세요."),
    ("사진을 여러 번 올렸는데 분석 비용이 안 늘어남", "정상입니다 — 같은 사진(내용 해시 기준)은 평생 한 번만 분석되고, 이후 재사용은 토큰 0입니다."),
    ("네이버 게시가 자동으로 [발행]까지 안 됨", "의도된 동작입니다. 네이버가 봇의 최종 발행을 차단하기 때문에 마지막 클릭은 항상 사람이 합니다."),
]
y = Inches(1.55)
for q, a in faqs2:
    add_rect(s, Inches(0.55), y, Inches(12.2), Inches(1.15), WHITE)
    add_text(s, Inches(0.8), y + Inches(0.08), Inches(11.6), Inches(0.4), f"Q. {q}", size=13, color=PRIMARY_DARK, bold=True)
    add_text(s, Inches(0.8), y + Inches(0.5), Inches(11.6), Inches(0.55), f"A. {a}", size=11.5, color=TEXT, line_spacing=1.2)
    y += Inches(1.25)

# ---- 마무리 ----
s = new_slide(PRIMARY_DARK)
next_page()
add_rect(s, 0, 0, Inches(0.22), SH, ACCENT)
add_text(s, Inches(0.9), Inches(2.6), Inches(11), Inches(0.5), "더 알아보기", size=16, color=ACCENT, bold=True)
add_text(s, Inches(0.85), Inches(3.05), Inches(11.5), Inches(1.1), "참고 문서", size=32, color=WHITE, bold=True)
add_bullets(s, Inches(0.9), Inches(4.15), Inches(11.2), Inches(2.2), [
    (0, "README.md — 아키텍처와 설계 이유", False),
    (0, "CLAUDE.md — 내 회사에 맞게 커스터마이징하는 전체 절차", False),
    (0, "views/06_settings.py — 이 문서의 네이버 API 설명이 나온 실제 화면 코드", False),
], size=15, color=RGBColor(0xF0, 0xE3, 0xE8), space_after=14, muted=RGBColor(0xD8, 0xC6, 0xCE))

out_path = "/Users/jwlee/project/OSMU_thestitch/docs/OSMU_사용자_가이드.pptx"
prs.save(out_path)
print("Saved:", out_path, "| slides:", len(prs.slides._sldIdLst))
