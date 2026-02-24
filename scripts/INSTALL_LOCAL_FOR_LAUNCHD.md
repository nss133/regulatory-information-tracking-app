# launchd용 로컬 설치 (iCloud에서 실행 안 될 때)

macOS는 보안상 **launchd가 iCloud Drive(Library/Mobile Documents) 안의 스크립트 실행**을 막습니다.  
그래서 **프로젝트를 로컬 폴더로 복사**한 뒤, 그 경로로만 자동 실행하도록 하면 됩니다.

## 1. 로컬로 복사 (한 번만)

터미널에서:

```bash
cp -R "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping" /Users/nsss/news-clipping
```

- `~/news-clipping` 에 같은 내용이 복사됩니다.
- 원본(iCloud)은 그대로 두고, **자동 실행만 로컬 복사본**에서 하게 할 수 있습니다.

## 2. plist 경로 수정

`~/Library/LaunchAgents/com.dailybriefing.plist` 안의 경로를 **모두** 다음으로 바꿉니다.

- **기존**: `.../Library/Mobile Documents/.../news clipping/...`
- **변경**: `/Users/nsss/news-clipping/...`

(이미 수정된 plist 내용은 프로젝트에서 제공하는 예시를 참고하세요.)

## 3. 재부팅

한 번 재부팅한 뒤, 로그인 시·매일 8시에 로컬 복사본의 `run_daily.sh`가 실행됩니다.

## 4. 이후 작업 방식

- **Cursor에서 편집**: iCloud 쪽(`news clipping`)에서 계속 작업해도 됩니다.
- **자동 발송**: 로컬 복사본(`~/news-clipping`)이 매일 실행됩니다.
- **설정/코드 반영**: iCloud에서 수정한 뒤, 필요할 때마다 아래로 다시 복사하면 됩니다.

```bash
cp -R "/Users/nsss/Library/Mobile Documents/com~apple~CloudDocs/cursor/news clipping/"* /Users/nsss/news-clipping/
```
(또는 중요한 파일만 선택해서 복사해도 됩니다.)

