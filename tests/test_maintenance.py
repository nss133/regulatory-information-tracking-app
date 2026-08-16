import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.maintenance import rotate_logs, verify_sqlite_backup


class TestMaintenance(unittest.TestCase):
    def test_backup_quick_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            result = verify_sqlite_backup(
                path, now=datetime.now(timezone.utc)
            )
            self.assertTrue(result.exists)
            self.assertEqual(result.integrity, "ok")
            cache = path.with_name(f"{path.name}.integrity.json")
            self.assertTrue(cache.exists())
            # 같은 파일은 캐시된 검증 결과를 재사용한다.
            second = verify_sqlite_backup(path, now=datetime.now(timezone.utc))
            self.assertEqual(second.integrity, "ok")

    def test_log_rotation_keeps_numbered_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "briefing.out.log"
            log.write_text("1234567890", encoding="utf-8")
            rotated = rotate_logs(root, max_bytes=5, keep=2)
            self.assertEqual(rotated, ["briefing.out.log"])
            self.assertEqual(log.read_text(), "")
            self.assertEqual((root / "briefing.out.log.1").read_text(), "1234567890")


if __name__ == "__main__":
    unittest.main()
