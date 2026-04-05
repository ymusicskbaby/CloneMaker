"""
アメブロ（ameblo.jp）の記事一覧をページ送りで取得し、
各記事ページからタイトル・URL・本文を取り出す。

大量文字でもメモリに溜めず、記事ごとに UTF-8 テキストへ追記する。
"""

from __future__ import annotations

import csv
import logging
import re
import time
from collections.abc import Callable
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("clone_maker.scraper")

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
def parse_blog_id(url: str) -> str:
    """トップ URL（https://ameblo.jp/ブログID/ のみ）からブログ ID を取り出す。"""
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "ameblo.jp":
        raise ValueError("ameblo.jp のブログ URL を指定してください（例: https://ameblo.jp/your-id/）")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 1:
        raise ValueError(
            "アメブロのトップページの URL を指定してください（例: https://ameblo.jp/your-id/）"
        )
    blog_id = parts[0]
    if re.match(r"^entry-\d+\.html$", blog_id, re.I) or blog_id.lower() in (
        "entrylist.html",
        "entrylist",
    ):
        raise ValueError(
            "アメブロのトップページの URL を指定してください（例: https://ameblo.jp/your-id/）"
        )
    if blog_id in ("theme", "category", "blog_portal", "campaign_list", "official"):
        raise ValueError("個人ブログのトップ URL を指定してください")
    return blog_id


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": DEFAULT_UA,
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return s


def fetch_html(session: requests.Session, url: str, timeout: float = 30.0) -> str:
    logger.debug("GET %s", url)
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("HTTP 取得失敗 url=%s error=%s", url, e)
        raise
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _canonical_ameblo_url(list_url: str, href: str) -> str:
    u = urljoin(list_url, href.strip())
    if u.startswith("//"):
        u = "https:" + u
    return u


def extract_entry_urls_from_list_page(html: str, blog_id: str, list_url: str) -> list[str]:
    """1 枚の記事一覧 HTML から、そのページに載っている記事 URL を順序維持で重複除去。"""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "entry-" not in href:
            continue
        full = _canonical_ameblo_url(list_url, href)
        try:
            pu = urlparse(full)
        except Exception:
            continue
        parts = [p for p in pu.path.split("/") if p]
        if len(parts) < 2:
            continue
        if parts[0] != blog_id:
            continue
        if not re.match(r"entry-\d+\.html$", parts[1], re.I):
            continue
        host = (pu.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host != "ameblo.jp":
            continue
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def find_next_list_url(html: str, current_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    nxt = soup.select_one("a.js-paginationNext[href]")
    if nxt and nxt.get("href"):
        return urljoin(current_url, nxt["href"])
    link = soup.find("link", rel=lambda x: x and "next" in str(x).lower())
    if link and link.get("href"):
        return urljoin(current_url, link["href"])
    return None


def collect_all_entry_urls(
    blog_id: str,
    session: requests.Session,
    list_delay_sec: float,
    on_list_page: Optional[Callable[[int, int, int, str], None]] = None,
) -> list[str]:
    """
    記事一覧をページ単位で取得し、全記事 URL を返す（新しい記事が先の並びのまま）。
    on_list_page: (page_index, url_count_on_page, cumulative_urls, list_url) を通知。
    """
    ordered: list[str] = []
    seen: set[str] = set()
    list_url = f"https://ameblo.jp/{blog_id}/entrylist.html"
    page_idx = 0
    visited_lists: set[str] = set()

    while list_url:
        if list_url in visited_lists:
            break
        visited_lists.add(list_url)
        page_idx += 1
        html = fetch_html(session, list_url)
        page_urls = extract_entry_urls_from_list_page(html, blog_id, list_url)
        for u in page_urls:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        if on_list_page:
            on_list_page(page_idx, len(page_urls), len(ordered), list_url)
        list_url = find_next_list_url(html, list_url) or ""
        if list_url:
            time.sleep(list_delay_sec)
    logger.info(
        "記事一覧の URL 収集完了 blog_id=%s 件数=%d",
        blog_id,
        len(ordered),
    )
    return ordered


def parse_entry_page(html: str, page_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one(".skinArticleTitle, h1.skinArticleTitle")
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
    body_el = soup.select_one("#entryBody, [data-uranus-component='entryBody']")
    if not body_el:
        body_el = soup.select_one(".skinArticleBody")
    body = ""
    if body_el:
        for bad in body_el.select("script, style, noscript"):
            bad.decompose()
        body = body_el.get_text("\n", strip=True)
    if not title:
        title = "(無題)"
    return title, body


def export_blog_to_files(
    blog_top_url: str,
    csv_path: str,
    txt_path: str,
    *,
    list_delay_sec: float = 0.4,
    entry_delay_sec: float = 0.5,
    on_list_page: Optional[Callable[[int, int, int, str], None]] = None,
    on_entry: Optional[Callable[[int, int, str, str], None]] = None,
) -> tuple[int, str]:
    """
    全記事を CSV（タイトル・URL）と TXT（本文の連結）に書き出す。
    戻り値: (記事数, ブログ ID)
    on_entry: (index, total, title, url) — 1 始まり index
    """
    blog_id = parse_blog_id(blog_top_url)
    session = _session()
    urls = collect_all_entry_urls(
        blog_id, session, list_delay_sec=list_delay_sec, on_list_page=on_list_page
    )
    total = len(urls)
    logger.info(
        "本文取得開始 blog_id=%s 記事数=%d csv=%s txt=%s",
        blog_id,
        total,
        csv_path,
        txt_path,
    )
    sep = "\n" + ("=" * 72) + "\n"

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as cf, open(
        txt_path, "w", encoding="utf-8", newline="\n"
    ) as tf:
        writer = csv.writer(cf)
        writer.writerow(["タイトル", "URL"])
        cf.flush()

        tf.write(
            f"# アメブロ本文まとめ（UTF-8）\n# ブログ: https://ameblo.jp/{blog_id}/\n# 記事数: {total}\n\n"
        )
        tf.flush()

        for i, url in enumerate(urls, start=1):
            html = fetch_html(session, url)
            title, body = parse_entry_page(html, url)
            writer.writerow([title, url])
            cf.flush()
            tf.write(f"【{i} / {total}】{title}\n\n{body}\n{sep}")
            tf.flush()
            if on_entry:
                on_entry(i, total, title, url)
            if i == 1 or i == total or (total > 0 and i % max(1, total // 20) == 0):
                logger.info(
                    "本文取得中 %d/%d title=%s",
                    i,
                    total,
                    (title[:60] + "…") if len(title) > 60 else title,
                )
            if i < total:
                time.sleep(entry_delay_sec)
    logger.info("本文取得完了 blog_id=%s 件数=%d", blog_id, total)
    return total, blog_id
