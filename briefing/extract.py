from __future__ import annotations

import re
from typing import TYPE_CHECKING

from briefing.http import HttpClient
from briefing.sources.html import soupify
from briefing.utils import normalize_ws

if TYPE_CHECKING:
    from bs4 import BeautifulSoup


def _text_from_soup(soup: "BeautifulSoup") -> str:
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_ws(text)


def extract_main_text(http: HttpClient, url: str) -> str:
    """
    사이트별 정교한 본문 파서까지 만들면 유지보수 비용이 커서,
    MVP에서는 공통(보수적) 추출만 제공합니다.
    """
    html = http.get_text(url)
    soup = soupify(html)
    return _text_from_soup(soup)


def extract_page_content(
    http: HttpClient, url: str
) -> tuple[str, list[tuple[str, str]]]:
    """
    페이지를 한 번 fetch하여 본문 텍스트와 첨부파일 링크를 함께 반환합니다.
    Returns: (body_text, [(label, attachment_url), ...])
    """
    from briefing.kordoc import find_attachment_links

    html = http.get_text(url)
    soup = soupify(html)
    attachment_links = find_attachment_links(soup, base_url=url)
    body_text = _text_from_soup(soup)
    return body_text, attachment_links

