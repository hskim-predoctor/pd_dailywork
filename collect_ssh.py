#!/usr/bin/env python3
"""원격 서버의 git 커밋을 SSH로 수집한다.

Remote-SSH 로 작업한 서버의 저장소는 맥에 사본이 없다. 삼바로 마운트해
훑는 방법도 있으나, 끊긴 마운트에서 os.walk 가 무한정 블록돼 자동 실행이
멈춘다. 여기서는 로그 텍스트만 받아온다 — 원격에서 find + git log 를 한 번에
돌리고 결과만 가져오므로 왕복이 1회다.

전제:
  - 키 인증이 설정돼 있어야 한다(launchd 는 비밀번호를 입력할 수 없다).
    ssh-copy-id <호스트> 로 등록.
  - 서버 시간대가 무엇이든 KST 기준으로 자르기 위해 --since/--until 에
    오프셋을 명시한다.

접속 실패는 치명적으로 다루지 않는다. VPN 이 끊긴 날 파이프라인 전체가
죽으면 안 되므로, 경고만 남기고 0건으로 넘어간다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime

from collect_mac import KST, day_bounds

# 원격에서 실행할 스크립트. $1=루트, $2=since, $3=until
REMOTE_SCRIPT = r"""
find "$1" -maxdepth 6 -name .git \
     -not -path '*/node_modules/*' -not -path '*/_deps/*' \
     -not -path '*/third_party/*' -not -path '*/vendor/*' 2>/dev/null |
while IFS= read -r g; do
  repo="${g%/.git}"
  echo "###REPO $repo"
  git -C "$repo" log --since="$2" --until="$3" \
      --pretty=format:"%H%x1f%an%x1f%aI%x1f%s" --shortstat 2>/dev/null
  echo
done
"""


def _parse(out: str, host: str, authors: list[str] | None) -> list[dict]:
    events: list[dict] = []
    repo = None
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur:
            if not authors or cur["author"] in authors:
                events.append(cur)
            cur = None

    for line in out.splitlines():
        if line.startswith("###REPO "):
            flush()
            repo = line[8:].strip()
        elif "\x1f" in line:
            flush()
            h, author, aiso, subject = line.split("\x1f", 3)
            cur = {
                "time": aiso, "source": "git-ssh",
                "project": repo.rsplit("/", 1)[-1] if repo else None,
                "host": host, "author": author, "commit": h[:10],
                "subject": subject, "stat": "",
            }
        elif line.strip() and cur is not None:
            cur["stat"] = line.strip()
    flush()
    return events


def collect_ssh(date_iso: str, repos: list[dict],
                authors: list[str] | None = None,
                timeout: int = 60) -> list[dict]:
    """repos = [{"host": "...", "path": "..."}, ...] 의 그날 커밋."""
    start, end, _ = day_bounds(date_iso)
    # 서버 시간대와 무관하게 KST 하루로 자르기 위해 오프셋을 명시
    since = start.isoformat()          # 2026-08-07T00:00:00+09:00
    until = (end.astimezone(KST)).isoformat()

    events: list[dict] = []
    for entry in repos:
        host, path = entry.get("host"), entry.get("path")
        if not host or not path:
            continue
        try:
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 host, "bash", "-s", "--", path, since, until],
                input=REMOTE_SCRIPT, capture_output=True, text=True,
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[경고] {host} 접속 실패 — 커밋 건너뜀: {e}", file=sys.stderr)
            continue
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            print(f"[경고] {host} 원격 실행 실패 — 커밋 건너뜀: "
                  f"{err[-1] if err else proc.returncode}", file=sys.stderr)
            continue
        events.extend(_parse(proc.stdout, host, authors))
    return sorted(events, key=lambda e: e["time"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--host", required=True)
    ap.add_argument("--path", required=True)
    args = ap.parse_args()

    _, _, date_iso = day_bounds(args.date)
    ev = collect_ssh(date_iso, [{"host": args.host, "path": args.path}])
    print(f"{date_iso}: {args.host} 커밋 {len(ev)}건")
    for e in ev:
        print(f"  [{e['project']}] {e['author']}: {e['subject']}  ({e['stat']})")


if __name__ == "__main__":
    main()
