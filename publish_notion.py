#!/usr/bin/env python3
"""요약 결과를 Notion 데이터베이스에 페이지로 발행한다.

Notion REST API(api.notion.com)를 stdlib urllib로 호출한다. 별도 의존성 없음.
API 버전은 2022-06-28로 고정.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# macOS python.org 배포본은 시스템 CA를 못 읽어 SSL 검증이 실패한다.
# certifi(anthropic SDK 의존성)가 있으면 그 번들을 쓰고, 없으면 기본값.
try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

SECTIONS = [
    ("done", "✅ 오늘 한 일"),
    ("decisions", "🧭 주요 결정"),
    ("blockers", "🚧 막힌 것"),
    ("next", "➡️ 내일 할 일"),
]


def _req(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(r, timeout=30, context=_SSL) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Notion API {e.code}: {e.read().decode()}") from e


def discover_title_prop(db_id: str, token: str) -> str:
    """DB에서 title 타입 속성 이름을 찾는다(이름이 '제목'이든 'Name'이든 대응)."""
    db = _req("GET", f"{API}/databases/{db_id}", token)
    for name, spec in db.get("properties", {}).items():
        if spec.get("type") == "title":
            return name
    raise RuntimeError("DB에 title 속성이 없습니다")


def _bullets(items: list[str]) -> list[dict]:
    return [{
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": it[:2000]}}]},
    } for it in items]


def build_page(summary: dict, date: str, author: str, host: str,
               db_id: str, props_cfg: dict, title_prop: str) -> dict:
    """Notion pages.create 요청 본문을 조립."""
    title = f"{date} · {author} 업무 요약"

    properties: dict = {
        title_prop: {"title": [{"type": "text", "text": {"content": title}}]},
    }
    # 설정된 속성이 DB에 있을 때만 채운다(없으면 발행 시 400이 나므로 호출측에서 보정)
    if props_cfg.get("date"):
        properties[props_cfg["date"]] = {"date": {"start": date}}
    if props_cfg.get("author"):
        properties[props_cfg["author"]] = {"select": {"name": author}}
    if props_cfg.get("project") and summary.get("projects"):
        properties[props_cfg["project"]] = {
            "multi_select": [{"name": p[:100]} for p in summary["projects"]]
        }

    children: list[dict] = [{
        "object": "block", "type": "callout",
        "callout": {"rich_text": [{"type": "text",
                    "text": {"content": summary.get("headline", "")[:2000]}}],
                    "icon": {"emoji": "📝"}},
    }]
    for key, heading in SECTIONS:
        items = summary.get(key) or []
        if not items:
            continue
        children.append({
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": heading}}]},
        })
        children.extend(_bullets(items))
    children.append({
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text",
                      "text": {"content": f"— {host} 에서 자동 생성"}}]},
    })

    return {"parent": {"database_id": db_id}, "properties": properties, "children": children}


def publish(summary: dict, *, date: str, author: str, host: str,
            db_id: str, token: str, props_cfg: dict) -> str:
    title_prop = discover_title_prop(db_id, token)
    page = build_page(summary, date, author, host, db_id, props_cfg, title_prop)
    res = _req("POST", f"{API}/pages", token, page)
    return res.get("url", "(url 없음)")
