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
               db_id: str, props_cfg: dict, title_prop: str,
               device: str | None = None) -> dict:
    """Notion pages.create 요청 본문을 조립.

    device(기기 이름)를 주면 제목과 `기기` 속성에 넣는다. 한 사람이 여러
    기기에서 발행할 때 같은 날짜 페이지를 구분하는 축이 된다.
    """
    dev = device or host
    title = f"{date} · {author} · {dev} 업무 요약"

    properties: dict = {
        title_prop: {"title": [{"type": "text", "text": {"content": title}}]},
    }
    if props_cfg.get("device"):
        properties[props_cfg["device"]] = {"select": {"name": dev[:100]}}
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
                      "text": {"content": f"— {dev} ({host}) 에서 자동 생성"}}]},
    })

    return {"parent": {"database_id": db_id}, "properties": properties, "children": children}


def publish(summary: dict, *, date: str, author: str, host: str,
            db_id: str, token: str, props_cfg: dict,
            device: str | None = None) -> tuple[str, str]:
    """새 페이지 생성. (page_id, url) 반환."""
    title_prop = discover_title_prop(db_id, token)
    page = build_page(summary, date, author, host, db_id, props_cfg, title_prop, device)
    res = _req("POST", f"{API}/pages", token, page)
    return res["id"], res.get("url", "(url 없음)")


def _clear_children(page_id: str, token: str) -> None:
    """페이지 본문 블록을 모두 지운다(갱신 전 비우기)."""
    while True:
        res = _req("GET", f"{API}/blocks/{page_id}/children?page_size=100", token)
        blocks = res.get("results", [])
        if not blocks:
            return
        for b in blocks:
            _req("DELETE", f"{API}/blocks/{b['id']}", token)
        if not res.get("has_more"):
            return


def update(summary: dict, *, date: str, author: str, host: str, page_id: str,
           db_id: str, token: str, props_cfg: dict,
           device: str | None = None) -> tuple[str, str]:
    """기존 페이지의 속성과 본문을 새 요약으로 교체. (page_id, url) 반환.

    페이지가 지워졌거나(404) 사용자가 보관 처리했으면 RuntimeError 를 올려
    호출측이 새로 만들도록 한다. 지운 페이지를 되살리지 않는다.
    """
    cur = _req("GET", f"{API}/pages/{page_id}", token)      # 404 면 여기서 예외
    if cur.get("archived") or cur.get("in_trash"):
        raise RuntimeError(f"페이지가 보관/삭제됨: {page_id}")

    title_prop = discover_title_prop(db_id, token)
    page = build_page(summary, date, author, host, db_id, props_cfg, title_prop, device)
    res = _req("PATCH", f"{API}/pages/{page_id}", token,
               {"properties": page["properties"]})
    _clear_children(page_id, token)
    _req("PATCH", f"{API}/blocks/{page_id}/children", token,
         {"children": page["children"]})
    return page_id, res.get("url", "(url 없음)")
