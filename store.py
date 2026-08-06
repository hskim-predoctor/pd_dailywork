#!/usr/bin/env python3
"""발행 이력을 날짜별로 로컬에 남긴다.

용도 두 가지:
  1) 같은 날짜를 다시 돌릴 때 Notion 페이지를 새로 만들지 않고 갱신 (멱등성)
  2) 전날 요약을 다음 날 프롬프트에 넣어 같은 내용이 반복 보고되는 것을 방지

파일: data/YYYY-MM-DD.json  (gitignore 대상 — 업무 내용이 들어간다)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


def _path(date_iso: str) -> Path:
    return DATA_DIR / f"{date_iso}.json"


def load(date_iso: str) -> dict | None:
    """해당 날짜의 발행 이력. 없으면 None."""
    p = _path(date_iso)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_previous(date_iso: str, lookback: int = 7) -> dict | None:
    """직전에 발행된 이력. 하루 건너뛴 날이 있어도 거슬러 올라가 찾는다."""
    d = date.fromisoformat(date_iso)
    for back in range(1, lookback + 1):
        rec = load((d - timedelta(days=back)).isoformat())
        if rec:
            return rec
    return None


def save(date_iso: str, *, title: str, summary: dict, host: str,
         page_id: str | None, url: str | None) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    p = _path(date_iso)
    p.write_text(json.dumps({
        "date": date_iso,
        "title": title,
        "summary": summary,
        "host": host,
        "notion_page_id": page_id,
        "notion_url": url,
        "published_at": datetime.now().astimezone().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
