#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(pwd)"
[[ -x "$ROOT_DIR/.venv/bin/python" ]] || ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
CONFIG="${1:-config.yaml}"

# 브리핑을 보내지 않는 주말에는 미실행 경고도 만들지 않는다.
if (( 10#$(date +%u) >= 6 )); then
  echo "INFO: 주말에는 브리핑 상태 감시를 건너뜁니다."
  exit 0
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: 운영 가상환경을 찾을 수 없습니다: $VENV_PYTHON" >&2
  exit 1
fi

cd "$ROOT_DIR"

# 감시 시점에도 전날/당일 상태가 보이도록 대시보드를 한 번 더 갱신한다.
"$VENV_PYTHON" -m briefing operations --config "$CONFIG" --rotate-logs || \
  echo "WARN: 운영 대시보드/백업 점검 실패" >&2

# 관리자 경고 메일용 SMTP 비밀번호를 Keychain에서 명시적으로 로드한다.
read -r ACCOUNT PASSWORD_ENV <<<"$("$VENV_PYTHON" -c "
import yaml
try:
    smtp = yaml.safe_load(open('$CONFIG')).get('email', {}).get('smtp', {})
    print((smtp.get('user', '') or ''), (smtp.get('password_env', '') or ''))
except Exception:
    print('', '')
" 2>/dev/null)"
if [[ -n "$ACCOUNT" && -n "$PASSWORD_ENV" && -z "${!PASSWORD_ENV:-}" ]]; then
  PASSWORD="$(security find-generic-password \
    -s 'DailyRegulatoryBriefing:GMAIL_APP_PASSWORD' \
    -a "$ACCOUNT" -w 2>/dev/null || true)"
  if [[ -n "$PASSWORD" ]]; then
    export "${PASSWORD_ENV}=${PASSWORD}"
  fi
fi

"$VENV_PYTHON" -m briefing delivery-watch --config "$CONFIG"
