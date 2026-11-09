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
