#!/usr/bin/env python3
"""하루치 작업 로그 수집기 (맥).

Claude Code 세션(JSONL)과 git 커밋을 읽어 그날의 이벤트를
정규화된 JSON 하나로 만든다. 서버로 푸시하기 전 단계.

사용:
    python3 collect_mac.py                 # 오늘(KST) 수집 → stdout
    python3 collect_mac.py --date 2026-07-09
    python3 collect_mac.py -o today.json    # 파일로 저장
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HOME = Path.home()

# 수집 대상 --------------------------------------------------------------
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
# git 저장소를 찾을 루트들 (하위 3단계까지 .git 탐색)
REPO_ROOTS = [HOME / "predoctor_workspace"]
# 요약 재료로 너무 큰 도구 출력은 자른다
MAX_TEXT = 2000


def day_bounds(date_str: str | None) -> tuple[datetime, datetime, str]:
    """해당 날짜(KST)의 [00:00, 24:00) 경계를 UTC-aware로 반환."""
    if date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        d = datetime.now(KST).date()
    start = datetime.combine(d, time.min, tzinfo=KST)
    return start, start + timedelta(days=1), d.isoformat()


def flatten_content(content) -> str:
    """message.content(문자열 또는 블록 배열)에서 사람이 읽을 텍스트만 추출."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            parts.append(block.get("text", ""))
        elif t == "tool_use":
            parts.append(f"[도구:{block.get('name','?')}]")
        # tool_result 등 대용량 출력은 스킵
    return "\n".join(p for p in parts if p).strip()


def collect_claude(start: datetime, end: datetime) -> list[dict]:
    """기간 내 user/assistant 메시지를 이벤트로 수집."""
    events: list[dict] = []
    if not CLAUDE_PROJECTS.exists():
        return events
    for jf in CLAUDE_PROJECTS.rglob("*.jsonl"):
        try:
            lines = jf.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") not in ("user", "assistant"):
                continue
            ts_raw = d.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (start <= ts < end):
                continue
            msg = d.get("message") or {}
            text = flatten_content(msg.get("content"))
            if not text:
                continue
            events.append({
                "time": ts.astimezone(KST).isoformat(),
                "source": "claude-code",
                "role": msg.get("role", d.get("type")),
                "project": Path(d.get("cwd", "")).name or None,
                "branch": d.get("gitBranch") or None,
                "session": d.get("sessionId"),
                "text": text[:MAX_TEXT],
            })
    return events


def find_repos() -> list[Path]:
    repos: list[Path] = []
    for root in REPO_ROOTS:
        if not root.exists():
            continue
        for gitdir in root.glob("*/.git"):
            repos.append(gitdir.parent)
        for gitdir in root.glob("*/*/.git"):
            repos.append(gitdir.parent)
    return sorted(set(repos))


def collect_git(date_iso: str) -> list[dict]:
    """그날 커밋을 이벤트로 수집 (작성자 무관, repo별)."""
    events: list[dict] = []
    since = f"{date_iso} 00:00:00"
    until = f"{date_iso} 23:59:59"
    for repo in find_repos():
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), "log",
                 f"--since={since}", f"--until={until}",
                 "--pretty=format:%H\x1f%an\x1f%aI\x1f%s", "--shortstat"],
                capture_output=True, text=True, timeout=15,
            ).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        cur = None
        for line in out.splitlines():
            if "\x1f" in line:
                if cur:
                    events.append(cur)
                h, author, aiso, subject = line.split("\x1f", 3)
                cur = {
                    "time": aiso, "source": "git", "project": repo.name,
                    "author": author, "commit": h[:10],
                    "subject": subject, "stat": "",
                }
            elif line.strip() and cur is not None:
                cur["stat"] = line.strip()
        if cur:
            events.append(cur)
    return events


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("-o", "--out", help="출력 파일 (기본: stdout)")
    args = ap.parse_args()

    start, end, date_iso = day_bounds(args.date)
    claude = collect_claude(start, end)
    git = collect_git(date_iso)
    claude.sort(key=lambda e: e["time"])
    git.sort(key=lambda e: e["time"])

    payload = {
        "host": socket.gethostname(),
        "date": date_iso,
        "generated_at": datetime.now(KST).isoformat(),
        "counts": {"claude": len(claude), "git": len(git)},
        "git": git,
        "claude": claude,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}  (claude={len(claude)}, git={len(git)})")
    else:
        print(text)


if __name__ == "__main__":
    main()
