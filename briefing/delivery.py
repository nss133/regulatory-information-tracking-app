from __future__ import annotations

import hashlib
import json
from pathlib import Path

from briefing.db import StoredItem
from briefing.reports import PdfAttachment


def delivery_fingerprint(items: list[StoredItem], recipients: list[str]) -> str:
    """동일 수신자에게 동일 버전 항목을 보내는 시도를 안정적으로 식별한다."""
    payload = {
        "recipients": sorted(address.strip().lower() for address in recipients),
        "items": sorted((item.id, item.content_hash) for item in items),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def archive_delivery(
    *,
    archive_root: str | Path,
    run_date: str,
    run_id: str,
    subject: str,
    message_id: str,
    recipients: list[str],
    html: str,
    items: list[StoredItem],
    attachments: tuple[PdfAttachment, ...] = (),
    editorial_issues: tuple = (),
    delivery_review: dict | None = None,
) -> Path:
    """실제 SMTP에 넘길 HTML과 항목 원장을 같은 이름으로 보관한다."""
    day_dir = Path(archive_root) / run_date[:10]
    day_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{run_date[11:19].replace(':', '')}-{run_id[:8]}"
    html_path = day_dir / f"{stem}.html"
    json_path = day_dir / f"{stem}.json"
    html_path.write_text(html, encoding="utf-8")
    attachment_manifest = []
    for attachment in attachments:
        if Path(attachment.filename).name != attachment.filename:
            raise ValueError("잘못된 첨부파일명")
        target = day_dir / f"{stem}-{attachment.filename}"
        target.write_bytes(attachment.content)
        preview = None
        if attachment.preview_png:
            preview_name = f"{stem}-{attachment.report_id or attachment.sha256[:16]}-preview.png"
            (day_dir / preview_name).write_bytes(attachment.preview_png)
            preview = {"archive_file": preview_name, "cid": attachment.preview_cid,
                       "sha256": hashlib.sha256(attachment.preview_png).hexdigest()}
        attachment_manifest.append({"preview": preview, "filename": attachment.filename, "archive_file": target.name,
            "sha256": attachment.sha256, "size": len(attachment.content),
            "report_id": attachment.report_id, "item_ids": list(attachment.item_ids)})
    manifest = {
        "run_id": run_id,
        "run_date": run_date,
        "subject": subject,
        "message_id": message_id,
        "recipients": recipients,
        "html_file": html_path.name,
        "attachments": attachment_manifest,
        "editorial": [issue.manifest() for issue in editorial_issues],
        "delivery_review": delivery_review,
        "items": [
            {
                "id": item.id,
                "source": item.source,
                "category": item.category,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at,
                "content_hash": item.content_hash,
                "presentation": {key: getattr(item, key) for key in (
                    'importance', 'importance_reason', 'summary', 'attachments_json',
                    'last_sent_at', 'sent_hash', 'deadlines_json',
                    'relevance_score', 'relevance_evidence',
                )},
            }
            for item in items
        ],
    }
    json_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return json_path
