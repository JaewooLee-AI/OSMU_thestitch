"""Keyword-based news discovery (Google News RSS + Naver News search).

Complements the manual "paste a URL" flow in News Curation: the admin picks
SEO keywords (reused from Brand Kit) instead of hunting for article links
by hand. No API keys required for either source.

Naver News is scraped from the public search results page first (free), with
the NAVER API HUB news endpoint as a fallback when a key is registered in
⚙️ 설정 — see fetch_naver_news_search.
"""
from __future__ import annotations

import urllib.parse

import feedparser
import requests
from bs4 import BeautifulSoup

from core import repo

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch_google_news_rss(keyword: str, max_results: int = 5) -> list[dict]:
    """Google News RSS search — no API key needed, returns Korean results."""
    encoded = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        resp = requests.get(rss_url, headers=_HEADERS, timeout=10)
        feed = feedparser.parse(resp.content)
    except Exception:
        return []

    articles = []
    for entry in feed.entries[:max_results]:
        source = getattr(entry, "source", None)
        articles.append(
            {
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": source.get("title", "Google News") if source else "Google News",
                "published": entry.get("published", ""),
                "summary": BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(strip=True),
            }
        )
    return articles


def fetch_naver_news_search(keyword: str, max_results: int = 5) -> list[dict]:
    """Naver News results: the free public results page first, then the
    metered API HUB news endpoint if scraping finds nothing and a key is set.

    The scrape used to select `div.news_wrap` / `a.news_tit`. Naver replaced
    that markup with generated class names (e.g. `fender-ui_a82de4df`) that
    change between deploys, and since every failure was swallowed the source
    silently returned 0 articles for every keyword. The scraper now reads
    structure instead of class names; the API fallback covers the next time
    the structure changes too.
    """
    articles = _scrape_naver_news(keyword, max_results)
    if articles:
        return articles
    return _naver_news_api(keyword, max_results)


_NEW_WINDOW_SUFFIX = "새 창 열림"


def _scrape_naver_news(keyword: str, max_results: int) -> list[dict]:
    """Structural parse of the results list, independent of class names.

    Inside `.list_news` every article renders as external (non-naver.com)
    links to the same article URL — the headline first, the summary snippet
    second. Grouping by URL in document order recovers both.
    """
    try:
        resp = requests.get(
            "https://search.naver.com/search.naver",
            params={"where": "news", "query": keyword, "sort": "0"},
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        print(f"[news_search] Naver results page failed: {exc}")
        return []

    container = soup.select_one(".list_news") or soup
    by_url: dict[str, list[str]] = {}
    for a in container.find_all("a", href=True):
        href = a["href"]
        if not href.startswith(("http://", "https://")):
            continue
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith(("naver.com", "naver.net")):
            continue
        if parsed.path in ("", "/") and not parsed.query:
            continue  # the outlet's homepage (press-name link), not an article
        # Separator "" + whitespace normalisation: the query terms are wrapped
        # in highlight tags, and joining on " " split words around them.
        text = " ".join(a.get_text().split())
        if text.endswith(_NEW_WINDOW_SUFFIX):
            text = text[: -len(_NEW_WINDOW_SUFFIX)].strip()
        if len(text) < 8:
            continue  # press logos / "관련뉴스" chips, not headlines
        by_url.setdefault(href, []).append(text)

    articles = []
    for href, texts in by_url.items():
        host = urllib.parse.urlparse(href).netloc
        articles.append(
            {
                "title": texts[0],
                "url": href,
                "source": host.removeprefix("www.") or "Naver News",
                "published": "",
                "summary": texts[1] if len(texts) > 1 else "",
            }
        )
        if len(articles) >= max_results:
            break
    if not articles:
        print("[news_search] Naver results page parsed to 0 articles — markup may have changed")
    return articles


def _naver_news_api(keyword: str, max_results: int) -> list[dict]:
    """API HUB `/search/v1/news` — the same key and daily cap as the keyword
    research calls (ai_workers/keyword_research._request). Silently skipped
    when no key is saved; any API error just means no Naver results."""
    if not repo.get_naver_api_settings():
        return []
    from ai_workers import keyword_research

    try:
        payload = keyword_research._request(
            "/search/v1/news", params={"query": keyword, "display": max_results, "sort": "sim"}
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[news_search] Naver news API failed: {exc}")
        return []

    def _plain(markup: str) -> str:
        return BeautifulSoup(markup or "", "html.parser").get_text(strip=True)

    articles = []
    for item in payload.get("items") or []:
        url = item.get("originallink") or item.get("link") or ""
        if not url:
            continue
        articles.append(
            {
                "title": _plain(item.get("title")),
                "url": url,
                "source": urllib.parse.urlparse(url).netloc.removeprefix("www.") or "Naver News",
                "published": item.get("pubDate", ""),
                "summary": _plain(item.get("description")),
            }
        )
    return articles[:max_results]


def search_news_by_keywords(keywords: list[str], limit_per_keyword: int = 2) -> list[dict]:
    """Searches Google News + Naver News for each keyword, dedups against
    URLs already queued in osmu_campaigns, and dedups results against each
    other by URL."""
    known_urls = repo.list_known_source_urls()
    seen_urls = set(known_urls)
    results = []

    for keyword in keywords:
        for article in fetch_google_news_rss(keyword, max_results=limit_per_keyword) + fetch_naver_news_search(
            keyword, max_results=limit_per_keyword
        ):
            url = article.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            article["matched_keyword"] = keyword
            results.append(article)

    return results
