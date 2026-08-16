from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BackupStatus:
    path: str
    exists: bool
    size_bytes: int
    modified_at: str | None
    age_days: float | None
    integrity: str


def verify_sqlite_backup(path: str | Path, *, now: datetime | None = None) -> BackupStatus:
    backup = Path(path)
    if not backup.exists():
        return BackupStatus(str(backup), False, 0, None, None, "missing")
    stat = backup.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    current = now or datetime.now(timezone.utc)
    cache_path = backup.with_name(f"{backup.name}.integrity.json")
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            cached.get("size_bytes") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
            and cached.get("integrity")
        ):
            return BackupStatus(
                path=str(backup), exists=True, size_bytes=stat.st_size,
                modified_at=modified.isoformat(),
                age_days=max(0.0, (current - modified).total_seconds() / 86400),
                integrity=str(cached["integrity"]),
            )
    except (OSError, ValueError, TypeError):
        pass
    integrity = "error"
    try:
        uri = f"file:{backup.resolve()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            integrity = str(row[0]) if row else "no_result"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        integrity = f"error: {exc}"
    try:
        cache_path.write_text(
            json.dumps(
                {
                    "size_bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "integrity": integrity,
                    "checked_at": current.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return BackupStatus(
        path=str(backup), exists=True, size_bytes=stat.st_size,
        modified_at=modified.isoformat(),
        age_days=max(0.0, (current - modified).total_seconds() / 86400),
        integrity=integrity,
    )


def rotate_logs(
    log_dir: str | Path, *, max_bytes: int = 5 * 1024 * 1024, keep: int = 5
) -> list[str]:
    root = Path(log_dir)
    if not root.exists():
        return []
    rotated: list[str] = []
    for path in sorted(root.glob("*.log")):
        if not path.is_file() or path.stat().st_size < max_bytes:
            continue
        oldest = path.with_name(f"{path.name}.{keep}")
        if oldest.exists():
            oldest.unlink()
        for index in range(keep - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.rename(path.with_name(f"{path.name}.{index + 1}"))
        path.rename(path.with_name(f"{path.name}.1"))
        path.touch()
        rotated.append(path.name)
    return rotated
