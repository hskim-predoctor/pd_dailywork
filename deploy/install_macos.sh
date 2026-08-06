#!/bin/bash
# worklog_daily 를 launchd 에 등록한다 (매일 00:05, 어제 하루치 요약).
# 경로/인터프리터를 실행 시점에 찾아 plist 를 생성하므로 기기가 달라도 그대로 쓴다.
#
#   ./deploy/install_macos.sh            # 설치 또는 갱신
#   ./deploy/install_macos.sh --uninstall
set -euo pipefail

LABEL="ai.predoctor.worklog-daily"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "제거 완료: $LABEL"
    exit 0
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- 사전 점검 -------------------------------------------------------------
PYTHON="$(command -v python3 || true)"
[[ -n "$PYTHON" ]] || { echo "오류: python3 를 찾을 수 없습니다"; exit 1; }

[[ -f "$REPO/config.json" ]] || {
    echo "오류: $REPO/config.json 이 없습니다."
    echo "      cp config.example.json config.json 후 값을 채우세요."
    exit 1
}

# backend=cli 면 claude CLI 가 있어야 한다
BACKEND="$("$PYTHON" -c "import json;print(json.load(open('$REPO/config.json')).get('backend','cli'))")"
CLAUDE_DIR=""
if [[ "$BACKEND" == "cli" ]]; then
    CLAUDE_BIN="$(command -v claude || true)"
    [[ -n "$CLAUDE_BIN" ]] || {
        echo "오류: backend=cli 인데 claude CLI 가 없습니다."
        echo "      Claude Code 를 설치·로그인하거나 config 의 backend 를 api 로 바꾸세요."
        exit 1
    }
    CLAUDE_DIR="$(dirname "$CLAUDE_BIN"):"
fi

# launchd 는 PATH 를 거의 주지 않는다. git 과 claude 를 이름으로 호출하므로 명시한다.
LAUNCHD_PATH="${CLAUDE_DIR}/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>${LABEL}</string>

	<!-- 00:05 에 돌면서 --yesterday 로 방금 끝난 하루를 요약한다.
	     00:00 에 "오늘"을 수집하면 갓 시작한 날이라 항상 비고,
	     날짜를 실행 시점에 계산하므로 절전에서 늦게 깨어나도 대상이 맞다. -->
	<key>ProgramArguments</key>
	<array>
		<string>${PYTHON}</string>
		<string>${REPO}/run.py</string>
		<string>--yesterday</string>
	</array>

	<key>WorkingDirectory</key>
	<string>${REPO}</string>

	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>0</integer>
		<key>Minute</key>
		<integer>5</integer>
	</dict>

	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>${LAUNCHD_PATH}</string>
	</dict>

	<key>StandardOutPath</key>
	<string>${REPO}/logs/worklog.log</string>
	<key>StandardErrorPath</key>
	<string>${REPO}/logs/worklog.err.log</string>

	<key>RunAtLoad</key>
	<false/>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "등록 완료: $LABEL"
echo "  python  : $PYTHON"
echo "  repo    : $REPO"
echo "  backend : $BACKEND"
echo "  PATH    : $LAUNCHD_PATH"
echo
echo "즉시 1회 실행 : launchctl kickstart -p $DOMAIN/$LABEL"
echo "로그 확인     : tail -f $REPO/logs/worklog.log"
echo "제거          : ./deploy/install_macos.sh --uninstall"
