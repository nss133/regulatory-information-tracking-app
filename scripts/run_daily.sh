#!/usr/bin/env bash
set -euo pipefail

# launchd(WorkingDirectory) 또는 스크립트 경로 기준
ROOT_DIR="$(pwd)"
[[ -d "$ROOT_DIR/.venv" ]] || ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "ERROR: .venv가 없습니다. README의 설치 절차를 먼저 진행하세요." >&2
  exit 1
fi

source .venv/bin/activate

CONFIG="${1:-config.yaml}"
# 환경변수가 없으면 config의 smtp.user로 Keychain에서 읽어서 설정 (터미널/launchd 공통)
if [[ -z "${GMAIL_APP_PASSWORD:-}" ]] && [[ -f "$CONFIG" ]]; then
  account=$(python3 -c "
import yaml
try:
    c = yaml.safe_load(open('$CONFIG'))
    print(c.get('email', {}).get('smtp', {}).get('user', '') or '')
except Exception:
    print('')
" 2>/dev/null)
  if [[ -n "$account" ]]; then
    pw=$(security find-generic-password -s "DailyRegulatoryBriefing:GMAIL_APP_PASSWORD" -a "$account" -w 2>/dev/null || true)
    if [[ -n "$pw" ]]; then
      export GMAIL_APP_PASSWORD="$pw"
    fi
  fi
fi

python -m briefing run --config "$CONFIG"

