#!/usr/bin/env python3
"""하루치 수집 JSON을 Claude로 요약한다.

collect_mac.py가 만든 payload(git 커밋 + Claude 대화 이벤트)를 받아
구조화된 업무 요약(오늘 한 일 / 주요 결정 / 막힌 것 / 내일 할 일)을 돌려준다.

공식 anthropic SDK 사용. ANTHROPIC_API_KEY 환경변수 또는 `ant auth login`
프로필에서 자격증명을 자동으로 읽는다.
"""
from __future__ import annotations

import json

# 요약 결과 스키마 (Notion 발행이 이 구조에 의존)
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},                       # 하루 한 줄 요약
        "done": {"type": "array", "items": {"type": "string"}},       # 오늘 한 일
        "decisions": {"type": "array", "items": {"type": "string"}},  # 주요 결정
        "blockers": {"type": "array", "items": {"type": "string"}},   # 막힌 것
        "next": {"type": "array", "items": {"type": "string"}},       # 내일 할 일
        "projects": {"type": "array", "items": {"type": "string"}},   # 관련 프로젝트
    },
    "required": ["headline", "done", "decisions", "blockers", "next", "projects"],
    "additionalProperties": False,
}

SYSTEM = (
    "너는 개발자의 하루 작업 로그를 정리하는 비서다. "
    "git 커밋(실제 산출물)과 AI 코딩 대화(의도·과정)를 근거로 그날의 업무를 "
    "한국어로 요약한다. 커밋을 뼈대로 삼고 대화로 맥락을 보완하라. "
    "추측하지 말고 로그에 있는 것만 쓴다. 각 항목은 간결한 한 문장. "
    "해당 없는 배열은 빈 배열로 둔다."
)


def build_prompt(payload: dict, previous: dict | None = None) -> str:
    """수집 payload를 요약용 텍스트로 압축.

    previous(직전 발행 이력)를 주면 이미 보고된 내용을 프롬프트에 넣어
    같은 항목이 날마다 반복 보고되는 것을 막는다.
    """
    lines: list[str] = [f"날짜: {payload.get('date')}", ""]

    if previous and previous.get("summary"):
        prev, ps = previous, previous["summary"]
        lines.append(f"## 직전 보고({prev.get('date')})에 이미 실린 내용")
        lines.append(f"- 한줄: {ps.get('headline','')}")
        for key, label in (("done", "한 일"), ("decisions", "결정"),
                           ("next", "다음 할 일")):
            for item in (ps.get(key) or []):
                lines.append(f"- [{label}] {item}")
        lines.append("")
        lines.append("위는 **이미 보고된** 내용이다. 오늘 로그에 같은 작업이 이어지더라도 "
                     "그대로 반복하지 말고, 오늘 새로 진행된 부분만 쓴다. "
                     "다만 어제 '다음 할 일'로 적힌 것을 오늘 실제로 했다면 그건 "
                     "오늘의 '한 일'로 쓴다. 오늘 새로운 진전이 없으면 빈 배열로 둔다.")
        lines.append("")

    git = payload.get("git", [])
    lines.append(f"## Git 커밋 ({len(git)}개)")
    for g in git:
        lines.append(f"- [{g.get('project')}] {g.get('subject')}  ({g.get('stat','')})")
    if not git:
        lines.append("- (없음)")
    lines.append("")

    claude = payload.get("claude", [])
    lines.append(f"## Claude Code 대화 ({len(claude)}개, 시간순 발췌)")
    for e in claude:
        role = "나" if e.get("role") == "user" else "AI"
        text = " ".join(e.get("text", "").split())[:280]
        lines.append(f"- {e.get('time','')[11:16]} [{e.get('project')}] {role}: {text}")
    if not claude:
        lines.append("- (없음)")
    lines.append("")

    cursor = payload.get("cursor", [])
    lines.append(f"## Cursor 대화 ({len(cursor)}개)")
    for e in cursor:
        role = "나" if e.get("role") == "user" else "AI"
        text = " ".join(e.get("text", "").split())[:280]
        where = f"@{e['host']}" if e.get("host") else ""
        lines.append(f"- [{e.get('project') or '무제'}{where}] {role}: {text}")
    if not cursor:
        lines.append("- (없음)")

    lines.append("")
    lines.append("위 로그를 바탕으로 오늘의 업무 요약을 스키마에 맞춰 작성하라.")
    return "\n".join(lines)


def summarize(payload: dict, model: str = "claude-opus-5",
              api_key: str | None = None, previous: dict | None = None) -> dict:
    """payload를 요약해 구조화된 dict를 반환."""
    import anthropic  # 지연 임포트: --no-llm 모드에선 SDK 불필요

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        output_config={
            "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
            "effort": "medium",  # 하루 요약은 routine 작업 — 토큰 절약
        },
        messages=[{"role": "user", "content": build_prompt(payload, previous)}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _extract_json(text: str) -> dict:
    """모델 출력에서 JSON 오브젝트만 뽑아낸다(코드펜스/서두 텍스트 허용)."""
    t = text.strip()
    if t.startswith("```"):                      # ```json ... ``` 벗기기
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, depth = t.find("{"), 0                # 첫 균형 잡힌 { } 블록 스캔
    if start < 0:
        raise ValueError(f"JSON을 찾을 수 없음: {text[:200]}")
    for i, ch in enumerate(t[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(t[start:i + 1])
    raise ValueError(f"JSON이 닫히지 않음: {text[:200]}")


def summarize_cli(payload: dict, model: str | None = None,
                  timeout: int = 600, previous: dict | None = None) -> dict:
    """Claude Code CLI(`claude -p`)로 요약. API 크레딧 대신 구독을 사용한다."""
    import subprocess

    prompt = (
        f"{SYSTEM}\n\n{build_prompt(payload, previous)}\n\n"
        "결과는 아래 스키마의 JSON 오브젝트 하나만 출력하라. "
        "설명, 인사말, 코드펜스 없이 JSON만.\n"
        f"{json.dumps(SUMMARY_SCHEMA, ensure_ascii=False)}"
    )
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패(exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout)[:500]}")

    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI 오류: {str(envelope)[:500]}")
    return _extract_json(envelope["result"])


def stub_summary(payload: dict) -> dict:
    """LLM 없이 raw 데이터로 만든 자리표시 요약 (--no-llm 데모용)."""
    git = payload.get("git", [])
    projects = sorted({g.get("project") for g in git if g.get("project")})
    return {
        "headline": f"{payload.get('date')} · 커밋 {len(git)}건, "
                    f"AI 대화 {len(payload.get('claude', []))}건 (요약 미생성)",
        "done": [f"[{g.get('project')}] {g.get('subject')}" for g in git],
        "decisions": [],
        "blockers": [],
        "next": [],
        "projects": projects,
    }
