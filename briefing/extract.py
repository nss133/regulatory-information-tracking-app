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


# 본문 컨테이너 우선 선택자(구체적 → 일반 순).
# 정부기관 CMS는 메뉴·footer가 <nav> 등 시맨틱 태그가 아닌 div라 태그 제거만으로는
# 껍데기가 남는다(2026-07 금감원 보도자료 오염 사례). 매칭되고 텍스트가 충분하면
# 그 컨테이너 안만 본문으로 쓰고, 없으면 기존 전체 페이지 추출로 폴백.
_BODY_SELECTORS = (
    ".n-dbdata",  # 금감원 2026-09 KRDS 개편 이후 게시글 본문
    ".dbdata",    # 금감원(fss.or.kr) 게시글 본문
    ".krds-bd-view",  # 금감원 신형 게시글 전체(짧은 본문의 안전한 폴백)
    ".bd-view",   # 금감원 게시글 본문+메타(제목·등록일·첨부)
)
_BODY_MIN_CHARS = 80


def _select_body_root(soup: "BeautifulSoup") -> "BeautifulSoup":
    for sel in _BODY_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) >= _BODY_MIN_CHARS:
            return el
    return soup


def extract_text_from_html(html: str) -> str:
    soup = soupify(html)
    return _text_from_soup(_select_body_root(soup))


def extract_main_text(http: HttpClient, url: str) -> str:
    """
    사이트별 정교한 본문 파서까지 만들면 유지보수 비용이 커서,
    MVP에서는 공통(보수적) 추출만 제공합니다.
    """
    return extract_text_from_html(http.get_text(url))


def extract_page_content(
    http: HttpClient, url: str
) -> tuple[str, list[tuple[str, str]]]:
    """
    페이지를 한 번 fetch하여 본문 텍스트와 첨부파일 링크를 함께 반환합니다.
    첨부파일 링크는 본문 컨테이너 밖(첨부 목록 영역)에 있을 수 있어 전체 페이지에서 찾습니다.
    Returns: (body_text, [(label, attachment_url), ...])
    """
    from briefing.kordoc import find_attachment_links

    html = http.get_text(url)
    soup = soupify(html)
    attachment_links = find_attachment_links(soup, base_url=url)
    body_text = _text_from_soup(_select_body_root(soup))
    return body_text, attachment_links
