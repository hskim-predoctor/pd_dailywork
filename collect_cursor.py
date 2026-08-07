#!/usr/bin/env python3
"""Cursor 대화 로그 수집기.

Cursor는 대화를 SQLite 한 곳에 모아둔다(워크스페이스·원격호스트 구분 없이).
  ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb   (macOS)
  ~/.config/Cursor/User/globalStorage/state.vscdb                        (Linux)

구조:
  composerHeaders(테이블)          대화 색인. createdAt/lastUpdatedAt 컬럼
  cursorDiskKV: composerData:<id>  대화 본문(메시지 순서 포함)
  cursorDiskKV: bubbleId:<id>:<b>  개별 메시지

주의 두 가지:
  1) 개별 메시지에 타임스탬프가 없다. 시간은 대화 단위로만 있어서, 대화가
     여러 날에 걸치면 날짜 귀속이 뭉개진다. since 로 오래된 대화를 잘라낸다.
  2) Cursor 실행 중이면 DB가 잠긴다. 항상 복사본을 떠서 읽는다.

사용:
    python3 collect_cursor.py --date 2026-08-07
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from collect_mac import KST, day_bounds, MAX_TEXT

HOME = Path.home()
DB_CANDIDATES = [
    HOME / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",
    HOME / ".config/Cursor/User/globalStorage/state.vscdb",
]
# 원격 작업이면 파일 URI 에 ssh-remote+<host> 가 박힌다
HOST_RE = re.compile(r"ssh-remote(?:%2B|\+)([A-Za-z0-9_.\-]+)")


def find_db() -> Path | None:
    for p in DB_CANDIDATES:
        if p.exists():
            return p
    return None


def _bubble_text(d: dict) -> str:
    """버블에서 사람이 읽을 텍스트만. 도구 호출은 이름만 남긴다."""
    text = (d.get("text") or "").strip()
    if text:
        return text
    tool = d.get("toolFormerData")
    if isinstance(tool, dict):
        name = tool.get("name") or tool.get("tool") or "?"
        return f"[도구:{name}]"
    return ""


def collect_cursor(start: datetime, end: datetime,
                   since: str | None = None) -> list[dict]:
    """[start, end) 에 활동한 대화의 메시지를 이벤트로 반환.

    since(YYYY-MM-DD) 이전에 시작된 대화는 통째로 건너뛴다. 메시지에 시간이
    없어 오래된 대화를 건드리면 전체가 그날로 딸려 들어오기 때문이다.
    """
    src = find_db()
    if src is None:
        return []

    since_ms = None
    if since:
        since_ms = datetime.fromisoformat(since).replace(tzinfo=KST).timestamp() * 1000

    events: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "cursor.vscdb"
        try:
            shutil.copy(src, dst)            # 잠금 회피: 항상 복사본을 읽는다
        except OSError:
            return []
        db = sqlite3.connect(dst)

        # 대화별 시간 색인 (headers 가 더 정확하나 일부만 존재 → composerData 로 보완)
        times: dict[str, tuple[float | None, float | None]] = {}
        try:
            for cid, created, updated in db.execute(
                    "SELECT composerId, createdAt, lastUpdatedAt FROM composerHeaders"):
                times[cid] = (created, updated)
        except sqlite3.Error:
            pass

        start_ms, end_ms = start.timestamp() * 1000, end.timestamp() * 1000

        for key, val in db.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"):
            if not val:
                continue
            cid = key.split(":", 1)[1]
            try:
                conv = json.loads(val)
            except json.JSONDecodeError:
                continue

            created, updated = times.get(cid, (None, None))
            created = created or conv.get("createdAt")
            updated = updated or conv.get("lastUpdatedAt") or created
            if not updated:
                continue
            if not (start_ms <= updated < end_ms):     # 그날 활동한 대화만
                continue
            if since_ms and created and created < since_ms:
                continue                                # 기준일 이전 대화는 제외

            host = None
            m = HOST_RE.search(val)
            if m:
                host = m.group(1)

            # 메시지 순서는 conversation 배열이 들고 있다
            order = [b.get("bubbleId") for b in (conv.get("conversation") or [])
                     if isinstance(b, dict) and b.get("bubbleId")]
            rows = {}
            for bkey, bval in db.execute(
                    "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?",
                    (f"bubbleId:{cid}:%",)):
                if not bval:
                    continue
                try:
                    rows[bkey.rsplit(":", 1)[1]] = json.loads(bval)
                except json.JSONDecodeError:
                    continue
            if not order:
                order = list(rows)

            when = datetime.fromtimestamp(updated / 1000, KST).isoformat()
            for bid in order:
                d = rows.get(bid)
                if not d:
                    continue
                text = _bubble_text(d)
                if not text:
                    continue
                events.append({
                    "time": when,           # 대화 단위 시각(메시지별 시각이 없음)
                    "source": "cursor",
                    "role": "user" if d.get("type") == 1 else "assistant",
                    "project": conv.get("name") or None,
                    "host": host,
                    "session": cid,
                    "text": text[:MAX_TEXT],
                })
    return events


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--since", help="이 날짜 이전에 시작된 대화는 제외")
    args = ap.parse_args()

    start, end, date_iso = day_bounds(args.date)
    ev = collect_cursor(start, end, args.since)
    print(f"{date_iso}: Cursor 이벤트 {len(ev)}건")
    hosts = {e["host"] for e in ev if e["host"]}
    if hosts:
        print("원격 호스트:", ", ".join(sorted(hosts)))
    for e in ev[:10]:
        who = "나" if e["role"] == "user" else "AI"
        print(f"  [{who}] {' '.join(e['text'].split())[:110]}")


if __name__ == "__main__":
    main()
