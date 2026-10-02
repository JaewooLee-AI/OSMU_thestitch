"""소제목과 목록 — 블로그 본문이 '서술형 나열'이 되지 않게 하는 최소한의 구조.

The draft used to be told "마크다운 제목 기호(#)는 쓰지 말고, 문단으로만", and
the Naver paste turned every line into a plain <p>. With no way to express a
section or a list anywhere in the pipeline, every post came out as one run of
paragraphs, and the marketer was pasting each draft into Gemini to have it
"정리" — which in practice meant adding exactly the 소제목 and 목록 this
module now allows.

The markers are plain characters, not markdown, so the text reads correctly
wherever it ends up without conversion — the workbench editor, the '본문 복사'
button, a manual paste into Naver, or another LLM:

    ■ 소제목        a section heading, on its own line
    • 항목          a list item, on its own line

Models write markdown anyway ('## 제목', '**제목**', '- 항목'), so `normalize`
folds those into the markers after every LLM pass instead of trusting the
instruction. The Naver paste and the preview render heading lines bold.
"""
from __future__ import annotations

import html
import re

HEADING_MARK = "■"
BULLET_MARK = "•"

# '## 제목', '### 제목', '**제목**' or '■ 제목' alone on a line.
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
_BOLD_LINE_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")
_HEADING_LINE_RE = re.compile(rf"^\s*{HEADING_MARK}\s*(.+?)\s*$")
# '- 항목', '* 항목', '· 항목', '• 항목'. Numbered lists are left alone: '1.'
# reads fine as plain text and a year like '2026. 10.' must not become a bullet.
_BULLET_LINE_RE = re.compile(rf"^\s*[-*·{BULLET_MARK}]\s+(.+?)\s*$")
_INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# '멈추오니,,'처럼 겹친 쉼표, 말줄임표가 아닌 '..'. 교정 LLM이 놓친 실제 사례라
# 결정론적으로 정리합니다. '...'(말줄임표)는 그대로 둡니다.
_DOUBLE_COMMA_RE = re.compile(r",{2,}")
_DOUBLE_PERIOD_RE = re.compile(r"(?<!\.)\.\.(?!\.)")


def normalize(text: str) -> str:
    """Markdown headings/lists -> the plain markers; stray '**' and doubled
    punctuation removed."""
    out = []
    for line in (text or "").split("\n"):
        heading = _MD_HEADING_RE.match(line) or _BOLD_LINE_RE.match(line)
        if heading:
            out.append(f"{HEADING_MARK} {heading.group(1).strip()}")
            continue
        bullet = _BULLET_LINE_RE.match(line)
        if bullet:
            out.append(f"{BULLET_MARK} {bullet.group(1).strip()}")
            continue
        out.append(_INLINE_BOLD_RE.sub(r"\1", line))
    text = "\n".join(out)
    text = _DOUBLE_COMMA_RE.sub(",", text)
    return _DOUBLE_PERIOD_RE.sub(".", text)


def heading_text(line: str):
    """The heading's text if `line` is a heading line, else None."""
    match = _HEADING_LINE_RE.match(line or "")
    return match.group(1) if match else None


def strip_markers(text: str) -> str:
    """Body text without the markers — for length and similarity measures."""
    lines = []
    for line in (text or "").split("\n"):
        heading = heading_text(line)
        if heading is not None:
            lines.append(heading)
            continue
        bullet = _BULLET_LINE_RE.match(line)
        lines.append(bullet.group(1) if bullet else line)
    return "\n".join(lines)


def count_headings(text: str) -> int:
    return sum(1 for line in (text or "").split("\n") if heading_text(line) is not None)


def to_html(text: str) -> str:
    """One <p> per non-empty line, heading lines bold.

    Bold is the one piece of formatting Naver's SmartEditor reliably keeps on
    a rich paste; heading tags and font sizes are re-parsed into its own
    components unpredictably. The marker itself is kept so the published
    post looks like the preview and the plain-text copy. A blank paragraph
    before each heading (except the first line) gives the section a break.
    Escapes first, for the same reason the paste always has: a stray '<' or
    '&' would be parsed as markup and swallow the surrounding text.
    """
    parts = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if heading_text(line) is not None:
            if parts:
                parts.append("<p><br></p>")
            parts.append(f"<p><b>{html.escape(line)}</b></p>")
        else:
            parts.append(f"<p>{html.escape(line)}</p>")
    return "".join(parts)
