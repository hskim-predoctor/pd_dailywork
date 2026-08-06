#!/usr/bin/env python3
"""하루 업무 요약 파이프라인: 수집 → 요약(Claude) → Notion 발행.

  python3 run.py                      # 오늘, 실제 발행
  python3 run.py --date 2026-07-09    # 특정 날짜
  python3 run.py --no-llm --dry-run   # 자격증명 없이 흐름/출력만 확인
  python3 run.py --dry-run            # 요약까지 하고 Notion 발행은 생략(페이로드 출력)

설정은 config.json (없으면 config.example.json 참고). API 키는 config의
anthropic_api_key 또는 환경변수 ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import collect_mac
import summarize as S
import publish_notion as P

HERE = Path(__file__).resolve().parent


def load_config() -> dict:
    for name in ("config.json", "config.example.json"):
        p = HERE / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit("config.json 이 없습니다 (config.example.json 복사해서 채우세요)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--yesterday", action="store_true",
                    help="어제(KST)를 대상으로. 자정 직후 실행용")
    ap.add_argument("--dry-run", action="store_true", help="Notion 발행 생략, 페이로드만 출력")
    ap.add_argument("--no-llm", action="store_true", help="Claude 호출 없이 자리표시 요약 사용")
    args = ap.parse_args()

    if args.yesterday:
        if args.date:
            raise SystemExit("--date 와 --yesterday 는 함께 쓸 수 없습니다")
        args.date = (datetime.now(collect_mac.KST) - timedelta(days=1)).date().isoformat()

    cfg = load_config()
    if cfg.get("repo_roots"):
        collect_mac.REPO_ROOTS = [Path(os.path.expanduser(r)) for r in cfg["repo_roots"]]

    # 1) 수집
    start, end, date_iso = collect_mac.day_bounds(args.date)
    payload = {
        "host": __import__("socket").gethostname(),
        "date": date_iso,
        "git": sorted(collect_mac.collect_git(date_iso), key=lambda e: e["time"]),
        "claude": sorted(collect_mac.collect_claude(start, end), key=lambda e: e["time"]),
    }
    print(f"[수집] {date_iso}  git={len(payload['git'])}  claude={len(payload['claude'])}")

    # 2) 요약
    if args.no_llm:
        summary = S.stub_summary(payload)
        print("[요약] --no-llm: 자리표시 요약 사용")
    else:
        # backend: "cli" = Claude Code 구독 사용(기본), "api" = API 크레딧 사용
        backend = cfg.get("backend", "cli")
        model = cfg.get("model", "claude-opus-5")
        if backend == "cli":
            summary = S.summarize_cli(payload, model=model)
        else:
            api_key = cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
            summary = S.summarize(payload, model=model, api_key=api_key)
        print(f"[요약:{backend}] {summary['headline']}")

    # 3) 발행
    author = cfg.get("author", "unknown")
    if args.dry_run:
        title_prop = cfg.get("notion_props", {}).get("title", "제목")
        page = P.build_page(summary, date_iso, author, payload["host"],
                            cfg.get("notion_database_id", "DRY"),
                            cfg.get("notion_props", {}), title_prop)
        print("[dry-run] Notion 페이지 페이로드:")
        print(json.dumps(page, ensure_ascii=False, indent=2))
        return

    url = P.publish(summary, date=date_iso, author=author, host=payload["host"],
                    db_id=cfg["notion_database_id"], token=cfg["notion_token"],
                    props_cfg=cfg.get("notion_props", {}))
    print(f"[발행] {url}")


if __name__ == "__main__":
    main()
