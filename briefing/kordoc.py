from __future__ import annotations

import io
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from briefing.http import HttpClient

# kordoc CLI 경로 (config.yaml archive.kordoc_cli_path로 오버라이드)
DEFAULT_KORDOC_CLI = (
    "/Users/nsss/.claude/plugins/marketplaces/"
    "korean-law-marketplace/node_modules/kordoc/dist/cli.js"
)

_ATTACHMENT_EXTENSIONS = frozenset({
    ".hwp", ".hwpx", ".hwpml", ".pdf", ".xlsx", ".xls", ".docx", ".doc",
})

_DOWNLOAD_PATTERNS = (
    "/download/", "/fileDown", "/atchFileDown", "/getFile",
    "downloadBbs", "fileDownload", "download.do", "filedown",
)


def find_attachment_links(soup, base_url: str) -> list[tuple[str, str]]:
    """
    BeautifulSoup으로 파싱된 페이지에서 첨부파일 링크를 추출합니다.
    Returns: list of (label, absolute_url)
    """
    from urllib.parse import urljoin

    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue

        label = a.get_text(" ", strip=True) or href
        path_lower = href.lower().split("?")[0]

        is_attachment = any(path_lower.endswith(ext) for ext in _ATTACHMENT_EXTENSIONS) or any(
            pat in href for pat in _DOWNLOAD_PATTERNS
        )
        if not is_attachment:
            continue

        full_url = href if href.startswith("http") else urljoin(base_url, href)
        if full_url not in seen:
            seen.add(full_url)
            results.append((label[:200], full_url))

    return results


def extract_text_with_kordoc(
    file_path: Path, *, cli_path: str = DEFAULT_KORDOC_CLI
) -> tuple[str, Optional[str]]:
    """
    kordoc CLI로 파일에서 텍스트를 추출합니다.
    Returns: (extracted_text, error_message or None)
    """
    try:
        result = subprocess.run(
            ["node", cli_path, str(file_path), "--silent"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            return "", f"kordoc exit {result.returncode}: {err[:200]}"
        text = result.stdout.strip()
        if not text:
            return "", "kordoc 빈 출력"
        return text, None
    except subprocess.TimeoutExpired:
        return "", "kordoc 타임아웃 (60초)"
    except FileNotFoundError:
        return "", f"node 또는 kordoc CLI 없음: {cli_path}"
    except Exception as e:
        return "", f"kordoc 오류: {str(e)[:200]}"


def download_and_extract(
    http: HttpClient,
    url: str,
    *,
    cli_path: str = DEFAULT_KORDOC_CLI,
    sleep_seconds: float = 0.7,
) -> tuple[str, str, Optional[str]]:
    """
    URL에서 파일을 다운로드하고 kordoc으로 텍스트를 추출합니다.
    Returns: (mime_type, extracted_text, error_message or None)
    """
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    try:
        content, mime_type = http.get_bytes(url)
    except Exception as e:
        return "unknown", "", f"다운로드 실패: {str(e)[:200]}"

    if not content:
        return "unknown", "", "빈 응답"

    # HTML 반환 감지 (로그인 페이지 등)
    head = content[:512].lower()
    if b"<html" in head or b"<!doctype" in head:
        return "text/html", "", "HTML 응답 (로그인 필요 또는 잘못된 URL)"

    ext = _guess_extension(content, mime_type, url)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        text, error = extract_text_with_kordoc(tmp_path, cli_path=cli_path)
        # ZIP 번들 폴백: PK 아카이브가 HWPX로 오인되어 실패한 경우,
        # 압축을 풀어 내부 문서(.hwp/.pdf 등)를 각각 추출·병합한다.
        if error and content[:4] == b"PK\x03\x04":
            z_text, _z_error = _extract_zip_members(content, cli_path=cli_path)
            if z_text:
                text, error = z_text, None
    finally:
        tmp_path.unlink(missing_ok=True)

    return mime_type, text, error


def _guess_extension(content: bytes, mime_type: str, url: str) -> str:
    """Magic bytes / URL / MIME type으로 파일 확장자를 추측합니다."""
    # 1) URL 확장자 우선
    path = url.split("?")[0].lower()
    for ext in _ATTACHMENT_EXTENSIONS:
        if path.endswith(ext):
            return ext

    # 2) Magic bytes
    if content[:4] == b"PK\x03\x04":
        inner = content[:4096]
        if b"word/" in inner:
            return ".docx"
        if b"xl/" in inner:
            return ".xlsx"
        return ".hwpx"
    if len(content) >= 8 and content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return ".hwp"
    if content[:4] == b"%PDF":
        return ".pdf"

    # 3) MIME type 폴백
    _MIME_MAP = {
        "application/pdf": ".pdf",
        "application/vnd.hancom.hwp": ".hwp",
        "application/haansofthwp": ".hwp",
        "application/x-hwp": ".hwp",
        "application/vnd.hancom.hwpx": ".hwpx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "application/vnd.ms-excel": ".xls",
    }
    for mime, ext in _MIME_MAP.items():
        if mime in mime_type:
            return ext

    return ".bin"


def _extract_zip_members(
    content: bytes, *, cli_path: str = DEFAULT_KORDOC_CLI
) -> tuple[str, Optional[str]]:
    """
    ZIP 아카이브(.zip 번들)를 풀어 내부 문서를 각각 kordoc으로 추출·병합합니다.
    HWPX로 오인되었으나 실제로는 여러 문서를 담은 압축파일인 경우에 사용합니다.
    Returns: (merged_text, error_message or None)
    """
    parts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                ext = Path(name).suffix.lower()
                if ext not in _ATTACHMENT_EXTENSIONS:
                    continue
                try:
                    data = zf.read(name)
                except Exception:
                    continue
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    tmp.write(data)
                    inner_path = Path(tmp.name)
                try:
                    inner_text, _ = extract_text_with_kordoc(inner_path, cli_path=cli_path)
                    if inner_text:
                        parts.append(f"[{name}]\n{inner_text}")
                finally:
                    inner_path.unlink(missing_ok=True)
    except zipfile.BadZipFile:
        return "", "ZIP 형식 아님"
    except Exception as e:
        return "", f"ZIP 처리 오류: {str(e)[:150]}"

    if parts:
        return "\n\n".join(parts), None
    return "", "ZIP 내 추출 가능한 문서 없음"
