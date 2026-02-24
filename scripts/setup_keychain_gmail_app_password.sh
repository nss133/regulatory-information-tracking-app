#!/usr/bin/env bash
set -euo pipefail

# 1) Gmail 앱 비밀번호를 macOS Keychain에 저장합니다.
# 2) 이후에는 export 없이도 프로그램이 Keychain에서 자동으로 읽어 SMTP 로그인합니다.
#
# 저장 키:
# - service: DailyRegulatoryBriefing:GMAIL_APP_PASSWORD
# - account: config.yaml의 email.smtp.user (예: curraheec@gmail.com)
#
# 사용법:
#   ./scripts/setup_keychain_gmail_app_password.sh curraheec@gmail.com

SERVICE="DailyRegulatoryBriefing:GMAIL_APP_PASSWORD"
ACCOUNT="${1:-}"

if [[ -z "$ACCOUNT" ]]; then
  echo "사용법: $0 you@gmail.com" >&2
  exit 1
fi

echo "Keychain에 Gmail 앱 비밀번호를 저장합니다."
echo "- service: $SERVICE"
echo "- account: $ACCOUNT"
echo

read -rsp "Gmail 앱 비밀번호(16자리, 공백 포함 가능): " APP_PW
echo

# -U: 기존 항목이 있으면 업데이트
# -T /usr/bin/security: launchd로 실행될 때도 security 명령이 키체인을 읽을 수 있도록 허용
security add-generic-password -U -a "$ACCOUNT" -s "$SERVICE" -w "$APP_PW" -T /usr/bin/security >/dev/null

echo "완료. 이제 export 없이 실행 가능합니다:"
echo "  python -m briefing run --config config.yaml"

