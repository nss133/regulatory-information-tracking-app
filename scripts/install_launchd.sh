#!/usr/bin/env bash
set -euo pipefail

# iCloud 작업본을 macOS 자동 실행이 허용되는 로컬 폴더에 배포하고 launchd 하나로 통일한다.
# 기본은 변경 없는 점검 모드이며, 실제 반영은 --apply를 명시해야 한다.

APPLY=0
TARGET_DIR="/Users/$(id -un)/news-clipping"
RUN_HOUR=8
RUN_MINUTE=0
LABEL="com.dailybriefing"
WATCH_LABEL="com.dailybriefing.watch"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --target) TARGET_DIR="$2"; shift ;;
    --hour) RUN_HOUR="$2"; shift ;;
    --minute) RUN_MINUTE="$2"; shift ;;
    -h|--help)
      echo "사용법: $0 [--apply] [--target PATH] [--hour 8] [--minute 0]"
      exit 0
      ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
  shift
done

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_HOME_DIR="$(cd ~ && pwd)"
PLIST_PATH="$USER_HOME_DIR/Library/LaunchAgents/$LABEL.plist"
WATCH_PLIST_PATH="$USER_HOME_DIR/Library/LaunchAgents/$WATCH_LABEL.plist"
LIVE_DATA_DIR="$(readlink "$SOURCE_ROOT/data" 2>/dev/null || true)"
WATCH_HOUR=$(( (RUN_HOUR + 1) % 24 ))

if [[ "$TARGET_DIR" == "/" || "$TARGET_DIR" == "$USER_HOME_DIR" || -z "$TARGET_DIR" ]]; then
  echo "안전하지 않은 배포 경로입니다: $TARGET_DIR" >&2
  exit 2
fi
if [[ -z "$LIVE_DATA_DIR" || ! -d "$LIVE_DATA_DIR" ]]; then
  echo "공용 데이터 경로를 확인할 수 없습니다: $SOURCE_ROOT/data" >&2
  exit 2
fi
if ! [[ "$RUN_HOUR" =~ ^([0-9]|1[0-9]|2[0-3])$ ]]; then
  echo "hour는 0~23이어야 합니다." >&2
  exit 2
fi
if ! [[ "$RUN_MINUTE" =~ ^([0-9]|[1-5][0-9])$ ]]; then
  echo "minute은 0~59여야 합니다." >&2
  exit 2
fi

echo "소스 작업본 : $SOURCE_ROOT"
echo "자동실행본 : $TARGET_DIR"
echo "공용 DB 경로: $LIVE_DATA_DIR"
echo "실행 시각   : 평일 $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE")"
echo "상태 감시   : 평일 $(printf '%02d:00' "$WATCH_HOUR")"
echo "launchd     : $PLIST_PATH"
echo "watch       : $WATCH_PLIST_PATH"

if [[ "$APPLY" -ne 1 ]]; then
  echo
  echo "점검 모드입니다. 실제 반영: $0 --apply"
  exit 0
fi

mkdir -p "$TARGET_DIR" "$TARGET_DIR/logs" "$USER_HOME_DIR/Library/LaunchAgents"

# 실행에 필요한 코드·설정만 동기화한다. 운영 DB, 로그, 가상환경은 덮어쓰지 않는다.
rsync -a \
  --exclude '.git/' --exclude '.claude/' --exclude '.omc/' \
  --exclude '/.venv' --exclude '/data' --exclude '/data_backup' \
  --exclude '/logs' --exclude '__pycache__/' --exclude '*.pyc' \
  "$SOURCE_ROOT/" "$TARGET_DIR/"
chmod +x "$TARGET_DIR/scripts/run_daily.sh" "$TARGET_DIR/scripts/watch_daily.sh"

# 과거 로컬 DB는 삭제하지 않고 timestamp 백업 폴더로 이동한다.
if [[ -e "$TARGET_DIR/data" && ! -L "$TARGET_DIR/data" ]]; then
  LEGACY_DATA_DIR="$TARGET_DIR/data.legacy.$(date +%Y%m%d-%H%M%S)"
  mv "$TARGET_DIR/data" "$LEGACY_DATA_DIR"
  echo "기존 로컬 DB 보존: $LEGACY_DATA_DIR"
elif [[ -L "$TARGET_DIR/data" ]]; then
  CURRENT_LINK="$(readlink "$TARGET_DIR/data")"
  if [[ "$CURRENT_LINK" != "$LIVE_DATA_DIR" ]]; then
    unlink "$TARGET_DIR/data"
  fi
fi
if [[ ! -e "$TARGET_DIR/data" ]]; then
  ln -s "$LIVE_DATA_DIR" "$TARGET_DIR/data"
fi

VENV_REBUILD=0
if [[ -x "$TARGET_DIR/.venv/bin/python" && -f "$TARGET_DIR/.venv/bin/activate" ]]; then
  ACTIVATION_PREFIX="$(sed -n 's/^VIRTUAL_ENV="\(.*\)"$/\1/p' "$TARGET_DIR/.venv/bin/activate" | head -n 1)"
  if [[ -n "$ACTIVATION_PREFIX" && "$ACTIVATION_PREFIX" != "$TARGET_DIR/.venv" ]]; then
    VENV_REBUILD=1
    echo "다른 경로에서 복사된 가상환경 감지: $ACTIVATION_PREFIX"
  fi
fi
if [[ "$VENV_REBUILD" -eq 1 ]]; then
  LEGACY_VENV_DIR="$TARGET_DIR/.venv.legacy.$(date +%Y%m%d-%H%M%S)"
  mv "$TARGET_DIR/.venv" "$LEGACY_VENV_DIR"
  echo "기존 가상환경 보존: $LEGACY_VENV_DIR"
fi
if [[ ! -x "$TARGET_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$TARGET_DIR/.venv"
fi
"$TARGET_DIR/.venv/bin/python" -m pip install -q -r "$TARGET_DIR/requirements.txt"
(
  cd "$TARGET_DIR"
  .venv/bin/python -m unittest discover -s tests -q
)

PLIST_TEMP="$(mktemp)"
WATCH_PLIST_TEMP="$(mktemp)"
CRON_TEMP="$(mktemp)"
cleanup_temp_files() {
  rm -f "$PLIST_TEMP" "$WATCH_PLIST_TEMP" "$CRON_TEMP"
}
trap cleanup_temp_files EXIT

cat > "$PLIST_TEMP" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$TARGET_DIR/scripts/run_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$TARGET_DIR</string>
  <key>StandardOutPath</key><string>$TARGET_DIR/logs/briefing.out.log</string>
  <key>StandardErrorPath</key><string>$TARGET_DIR/logs/briefing.err.log</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$RUN_HOUR</integer><key>Minute</key><integer>$RUN_MINUTE</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$RUN_HOUR</integer><key>Minute</key><integer>$RUN_MINUTE</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$RUN_HOUR</integer><key>Minute</key><integer>$RUN_MINUTE</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$RUN_HOUR</integer><key>Minute</key><integer>$RUN_MINUTE</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$RUN_HOUR</integer><key>Minute</key><integer>$RUN_MINUTE</integer></dict>
  </array>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST_TEMP"

cat > "$WATCH_PLIST_TEMP" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$WATCH_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$TARGET_DIR/scripts/watch_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$TARGET_DIR</string>
  <key>StandardOutPath</key><string>$TARGET_DIR/logs/watch.out.log</string>
  <key>StandardErrorPath</key><string>$TARGET_DIR/logs/watch.err.log</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$WATCH_HOUR</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$WATCH_HOUR</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$WATCH_HOUR</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$WATCH_HOUR</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$WATCH_HOUR</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
plutil -lint "$WATCH_PLIST_TEMP"

if [[ -f "$PLIST_PATH" ]]; then
  cp -p "$PLIST_PATH" "$PLIST_PATH.backup.$(date +%Y%m%d-%H%M%S)"
fi
cp "$PLIST_TEMP" "$PLIST_PATH"
if [[ -f "$WATCH_PLIST_PATH" ]]; then
  cp -p "$WATCH_PLIST_PATH" "$WATCH_PLIST_PATH.backup.$(date +%Y%m%d-%H%M%S)"
fi
cp "$WATCH_PLIST_TEMP" "$WATCH_PLIST_PATH"

# 같은 스크립트를 호출하던 cron 항목은 백업 후 제거하여 launchd와 중복되지 않게 한다.
if crontab -l > "$CRON_TEMP" 2>/dev/null; then
  cp "$CRON_TEMP" "$TARGET_DIR/logs/crontab.backup.$(date +%Y%m%d-%H%M%S)"
  awk '!/news-clipping\/scripts\/run_daily\.sh/' "$CRON_TEMP" | crontab -
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$WATCH_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$WATCH_PLIST_PATH"

echo "설치 완료. 즉시 발송은 실행하지 않았으며 다음 예약 시각부터 동작합니다."
launchctl print "gui/$(id -u)/$LABEL" | sed -n '1,45p'
launchctl print "gui/$(id -u)/$WATCH_LABEL" | sed -n '1,45p'
