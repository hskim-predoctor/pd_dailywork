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
import os
import socket
import subprocess
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HOME = Path.home()

# 수집 대상 --------------------------------------------------------------
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
# git 저장소를 찾을 루트들 (하위 전체 깊이를 재귀 탐색)
REPO_ROOTS = [HOME / "predoctor_workspace"]
# 저장소 탐색 시 내려가지 않을 디렉터리.
# _deps/third_party 류는 CMake 등이 받아온 **외부 저장소**라 남의 커밋이 섞인다.
SKIP_DIRS = {"node_modules", "venv", "__pycache__", "dist", "build",
             "vendor", "Pods", "target", "site-packages",
             "_deps", "third_party", "thirdparty", "external", "extern",
             "subprojects", "Carthage"}
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


def _under(path_str: str, roots: list[Path]) -> bool:
    """path_str 이 roots 중 하나의 하위인지."""
    if not path_str:
        return False
    try:
        p = Path(path_str).resolve()
    except OSError:
        return False
    for r in roots:
        try:
            p.relative_to(r.expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def collect_claude(start: datetime, end: datetime,
                   roots: list[Path] | None = None) -> list[dict]:
    """기간 내 user/assistant 메시지를 이벤트로 수집.

    roots 를 주면 세션의 cwd 가 그 하위인 대화만 담는다. git 수집과
    감시 범위를 일치시켜, 업무 폴더 밖 개인 작업이 섞이지 않게 한다.
    """
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
            if roots is not None and not _under(d.get("cwd", ""), roots):
                continue                       # 감시 폴더 밖 세션은 제외
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
    """REPO_ROOTS 아래 **모든 깊이**의 git 저장소를 찾는다.

    서브모듈은 .git 이 파일이므로 디렉터리/파일 양쪽을 확인하고,
    저장소를 찾아도 하위를 계속 훑어 중첩 저장소를 놓치지 않는다.
    """
    repos: list[Path] = []
    for root in REPO_ROOTS:
        if not root.exists():
            continue
        for dirpath, dirnames, _ in os.walk(root):
            here = Path(dirpath)
            if (here / ".git").exists():          # 디렉터리(일반) 또는 파일(서브모듈)
                repos.append(here)
            # 숨김 디렉터리와 의존성/빌드 산출물은 내려가지 않는다
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in SKIP_DIRS]
    return sorted(set(repos))


def collect_git(date_iso: str, authors: list[str] | None = None) -> list[dict]:
    """그날 커밋을 이벤트로 수집 (repo별).

    authors 를 주면 그 이름/이메일의 커밋만 센다. 외부 저장소가 탐색에
    섞여 들어와도 남의 커밋이 내 업무일지에 들어가지 않게 하는 안전장치.
    """
    events: list[dict] = []
    # 오프셋을 명시해 기기 시간대와 무관하게 KST 하루로 자른다
    start, end, _ = day_bounds(date_iso)
    since, until = start.isoformat(), end.isoformat()
    author_args = [f"--author={a}" for a in (authors or [])]  # 여러 개면 OR
    for repo in find_repos():
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), "log",
                 f"--since={since}", f"--until={until}", *author_args,
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
