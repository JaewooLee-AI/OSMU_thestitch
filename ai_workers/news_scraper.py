"""Article scraping for the newsjacking track.

Reduced to just the scrape: in OSMU_admin this module also owned the whole
news generation pipeline and duplicated ~80% of manual_content_writer.py.
Both now share ai_workers/content_writer.py, and the only thing genuinely
specific to news is "turn a URL into title + body text", which is what's left
here.

A Google News RSS link is not an article — it is a redirect page that bounces
to the publisher. Two consequences drive the shape of this module:

  * The body has to be read **after** the bounce. Waiting only for
    `domcontentloaded` returns Google's interstitial, whose body is empty, so
    the scrape silently produced zero characters and the pipeline carried on
    with brand facts alone (see `ArticleTooThin`).
  * The resolved publisher URL is what belongs in the citation footer. The
    `news.google.com/rss/articles/CBMi...` form names the aggregator, not the
    outlet, and is opaque to a reader.
"""
from __future__ import annotations

from typing import NamedTuple

import requests
from bs4 import BeautifulSoup

# Below this the "article" is a redirect stub, a paywall notice or a cookie
# wall — not something a post can be written from. The newsjacking track
# treats it as a hard failure rather than falling through to brand facts,
# because the resulting post looks like a successful curation while having no
# connection to the article it cites.
MIN_ARTICLE_CHARS = 200

_AGGREGATOR_HOSTS = ("news.google.com",)


class ArticleTooThin(RuntimeError):
    """Raised when a source URL yields no usable article text."""


class ScrapedArticle(NamedTuple):
    title: str
    body: str
    url: str  # the publisher's URL, after any aggregator redirect


def scrape_article(url: str) -> ScrapedArticle:
    """Fetch an article, following aggregator redirects to the publisher.

    Raises `ArticleTooThin` when the result is too short to write from. The
    caller is expected to surface that to the marketer — a news post drafted
    without its news is the failure this replaces.
    """
    title, body, resolved = _scrape_with_requests(url)

    if len(body) < MIN_ARTICLE_CHARS or _is_aggregator(resolved):
        try:
            title, body, resolved = _scrape_with_playwright(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[news_scraper] Playwright scrape failed for {url}: {exc}")

    if len(body) < MIN_ARTICLE_CHARS:
        raise ArticleTooThin(
            f"기사 본문을 {len(body)}자밖에 읽지 못했습니다 (최소 {MIN_ARTICLE_CHARS}자). "
            "로그인·유료 기사이거나 자동 수집이 차단된 매체일 수 있습니다. "
            "원문에서 본문을 복사해 메모에 붙여 넣고 일반 생성으로 진행하세요."
        )

    return ScrapedArticle(title=title, body=body, url=resolved)


def _is_aggregator(url: str) -> bool:
    return any(host in (url or "") for host in _AGGREGATOR_HOSTS)


def _extract(html: str, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else url
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
    return title, "\n".join(p for p in paragraphs if len(p) > 30)


def _scrape_with_requests(url: str) -> tuple[str, str, str]:
    """Fast path — works for a direct publisher link, returns near-nothing for
    an aggregator redirect (which is what sends us to Playwright)."""
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    title, body = _extract(resp.text, url)
    # `resp.url` already reflects plain HTTP redirects; Google's is done in
    # JavaScript, so it stays on the aggregator host here.
    return title, body, resp.url


def _scrape_with_playwright(url: str) -> tuple[str, str, str]:
    from playwright.sync_api import sync_playwright

    from ai_workers.naver_publisher import launch_browser

    with sync_playwright() as p:
        # p.chromium.launch()는 Playwright 번들 Chromium(playwright install로
        # 따로 받아야 함)이 있어야만 동작한다. 이 프로젝트는 네이버 발행
        # 자동화(naver_publisher.launch_browser)에서 이미 시스템 Chrome을
        # 우선 쓰고 번들 Chromium은 마지막 폴백으로만 쓰는데, 여기는 그
        # 폴백 체인 없이 번들 Chromium만 바로 불러서 — 번들 Chromium이 설치
        # 안 된 환경(예: 이 Windows 개발 PC)에서는 launch() 자체가 예외를
        # 던지고, 그게 scrape_article()에서 조용히 삼켜져 "본문 0자" 에러로
        # 나타났다. 같은 폴백 로직을 재사용해 시스템 Chrome을 먼저 쓴다.
        browser = launch_browser(p, headless=True)
        page = browser.new_page()
        try:
            # "networkidle" frequently never fires on Google News redirect
            # pages even after the body has fully rendered; "domcontentloaded"
            # is the standard fix for that timeout pattern.
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if _is_aggregator(url):
                # The bounce to the publisher is a client-side navigation, so
                # it happens *after* domcontentloaded. Reading the body here
                # without waiting is what previously returned an empty string
                # on every Google News link.
                try:
                    page.wait_for_url(lambda u: not _is_aggregator(u), timeout=20000)
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception as exc:  # noqa: BLE001
                    print(f"[news_scraper] redirect from aggregator never completed: {exc}")
            resolved = page.url
            title, body = _extract(page.content(), resolved)
            if len(body) < MIN_ARTICLE_CHARS:
                # Some outlets build the article body from divs rather than
                # <p>, so the parser above finds nothing. Rendered text is the
                # coarser fallback, filtered by the same line-length rule:
                # menus, bylines and share buttons are short lines, article
                # prose is not, and the writer only gets the first 3,000
                # characters — spending them on nav chrome is what makes a
                # newsjacked post read as though it never saw the article.
                body = "\n".join(
                    line.strip()
                    for line in page.inner_text("body").splitlines()
                    if len(line.strip()) > 30
                )
            return title or resolved, body, resolved
        finally:
            browser.close()
