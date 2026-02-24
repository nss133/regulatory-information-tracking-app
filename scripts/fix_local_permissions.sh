#!/usr/bin/env bash
# 로컬 복사본(~/news-clipping)의 DB·폴더 쓰기 가능하도록 수정.
# 한 번만 실행하면 됩니다: ./scripts/fix_local_permissions.sh

set -euo pipefail
LOCAL="${1:-$HOME/news-clipping}"
if [[ ! -d "$LOCAL" ]]; then
  echo "폴더가 없습니다: $LOCAL" >&2
  exit 1
fi
echo "권한 수정: $LOCAL"
chmod -R u+w "$LOCAL/data" 2>/dev/null || true
if [[ -f "$LOCAL/data/briefing.sqlite3" ]]; then
  xattr -c "$LOCAL/data/briefing.sqlite3" 2>/dev/null || true
  chmod u+w "$LOCAL/data/briefing.sqlite3"
fi
echo "완료. 이제 /Users/nsss/news-clipping/scripts/run_daily.sh 를 실행해 보세요."
