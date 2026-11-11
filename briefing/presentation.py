"""메일의 내용과 링크를 유지하는 표시 전용 도우미."""
from __future__ import annotations

import re


def compact_attachments(attachments):
    """원래 이름은 접근성 설명에 남기고 형식·별첨 번호를 짧게 표시한다."""
    seen = {}
    result = []
    for attachment in attachments:
        original = attachment.label
        extension = re.search(r"\.(hwpx|hwp|pdf)\b", original, re.IGNORECASE)
        label = extension.group(1).upper() if extension else original
        if extension:
            supplement = re.search(r"(별첨|붙임)\s*(\d+)", original)
            if supplement:
                label = "".join(supplement.groups()) + " " + label
            elif "보도자료" in original:
                label = "보도자료 " + label
        seen[label] = seen.get(label, 0) + 1
        display = label if seen[label] == 1 else f"{label} {seen[label]}"
        result.append(dict(label=original, url=attachment.url, display=display))
    return result


def reference_attachments(attachments):
    """주요 항목의 첨부 역할을 표시한다. 같은 문서의 PDF/HWP 쌍은 PDF를 우선한다."""
    unique = {}
    for a in attachments:
        match = re.search(r"\.(pdf|hwpx|hwp)\b", a.label, re.I)
        stem = a.label[:match.start()] if match else a.url
        old = unique.get(stem)
        if old is None or (match and match[1].lower() == 'pdf'):
            unique[stem] = a
    result = []
    seen = set()
    for a in unique.values():
        if a.url in seen:
            continue
        seen.add(a.url)
        match = re.search(r"\.(pdf|hwpx|hwp)\b", a.label, re.I)
        role = next((label for pattern, label in (
            ('신구', '신구조문대비표'), ('전문', '개정안 전문'),
            ('공고', '공고문'), ('보도자료|\\(참고\\)', '보도자료'),
        ) if re.search(pattern, a.label)), None)
        if role and match:
            display = role + ' · ' + match[1].upper()
        elif match:
            display = a.label[:match.end()]
            display = re.sub(r'^\d{6,8}[\s_]*', '', display)
        else:
            display = {'파일다운로드': '첨부파일 내려받기', '문서뷰어': '첨부 문서 미리보기'}.get(a.label, a.label)
        result.append(dict(label=a.label, url=a.url, display=display))
    return result
