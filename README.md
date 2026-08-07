# worklog_daily

하루치 개발 로그(git 커밋 + Claude Code 대화)를 모아 Claude로 요약하고
Notion 데이터베이스에 자동 발행하는 백그라운드 프로그램.

중앙 서버가 없다. **각 기기가 자기 로그만 수집해 자기 이름으로 직접 발행**하고,
Notion DB의 `작성자` 속성으로 한 사람의 여러 기기 기록을 묶는다.

```
collect_mac.py    수집   git 커밋 + ~/.claude/projects/**/*.jsonl
collect_cursor.py 수집   Cursor 대화 (globalStorage/state.vscdb)
summarize.py      요약   claude CLI(구독) 또는 anthropic SDK(API)
publish_notion.py 발행   Notion REST (stdlib만 사용)
store.py          이력   data/YYYY-MM-DD.json (중복 방지 + 직전 대조)
run.py            오케스트레이터
```

## 원격 서버 작업은 서버에 설치하지 않는다

VSCode/Cursor Remote-SSH로 서버에 붙어 작업하면 **AI 대화는 클라이언트(맥)에
쌓인다.** 서버에 수집기를 설치할 이유가 없고, 컨테이너에 붙어 작업하는 경우
서버에 설치해도 컨테이너 안 저장소는 보이지 않는다. 맥 한 곳에서 모은다.

서버의 git 커밋은 맥에 사본이 없으므로 잡히지 않는다. 커밋을 GitHub에 push하고
그쪽에서 가져오거나, SSH로 원격 `git log`를 돌리는 방식이 필요하다(미구현).

**서버 터미널에서 Claude Code를 쓰면 그 JSONL은 서버에 남아 수집되지 않는다.**

## 설치

### 1. Notion DB 준비

데이터베이스를 만들고 아래 속성을 **유형까지 맞춰** 생성한다.
이름은 `config.json`의 `notion_props`로 바꿀 수 있지만 유형은 고정이다.

| 속성 | 유형 |
|---|---|
| 이름 | 제목 (Title) |
| 날짜 | 날짜 (Date) |
| 작성자 | **선택 (Select)** — People 아님 |
| 프로젝트 | **다중 선택 (Multi-select)** |
| 기기 | **선택 (Select)** |

**DB 하나를 공유**하고 `작성자`(사람)와 `기기`(장비)로 구분한다. 중앙 서버나
취합 과정이 없다 — 각 기기가 같은 DB에 직접 쓰고, Notion이 취합 지점이다.

한 사람이 여러 기기를 쓰면 **하루에 기기 수만큼 페이지가 생긴다.** 중복 방지는
기기별 로컬 `data/`를 기준으로 하므로, 맥이 발행한 것을 서버는 알지 못한다.
하루 한 장으로 합치려면 별도 병합 로직이 필요하고 동시 실행 시 경쟁이 생기므로,
기기별로 나누고 Notion 뷰에서 그룹핑하는 쪽을 권한다.

### 2. 토큰 발급

https://www.notion.so/developers/tokens 에서 토큰 생성 → `ntn_`으로 시작하는 값을 복사.
개인 토큰(PAT)은 만든 사람 권한으로 동작하므로 페이지에 별도 연결이 필요 없다.
(팀 공용 integration을 쓸 경우에만 페이지 `···` → `연결 추가` 필요.)

### 3. 설정

```bash
cp config.example.json config.json    # config.json 은 gitignore 대상
chmod 600 config.json                 # 토큰이 들어가므로
```

| 키 | 설명 |
|---|---|
| `author` | 이름. Notion `작성자` 값이 된다 |
| `device` | 기기 이름. Notion `기기` 값이자 제목의 일부. 비우면 호스트명 |
| `backend` | `cli` = Claude Code 구독 사용(기본) / `api` = Anthropic 크레딧 사용 |
| `anthropic_api_key` | `backend: "api"` 일 때만 필요 |
| `notion_token` | 각자 발급한 토큰 |
| `notion_database_id` | 공유 DB의 32자 hex |
| `repo_roots` | 감시할 상위 폴더들. 하위 **전체 깊이**를 재귀 탐색한다 |
| `git_authors` | 본인 커밋만 세기 위한 이름/이메일 목록. 비우면 필터 해제 |
| `scope_claude_to_roots` | `true`(기본)면 `repo_roots` 안 세션의 대화만 수집 |
| `collect_cursor` | Cursor 대화 수집 여부 (기본 `true`) |
| `cursor_since` | 이 날짜 이전에 **시작된** 대화는 제외. 아래 설명 참고 |

### `cursor_since` 가 필요한 이유

Cursor는 개별 메시지에 타임스탬프를 남기지 않는다. 시간은 대화 단위로만 있어서,
**1년 된 대화를 오늘 한 번 건드리면 그 대화 전체가 오늘 요약에 실린다.**
기준일을 두면 그 이전에 시작된 대화는 통째로 제외된다.

대화의 36%가 하루를 넘긴다(최장 39일). 여러 날에 걸친 대화는 마지막 활동일에
귀속되므로 앞선 날의 내용이 섞일 수 있는데, 직전 발행분과 대조하는 로직이
이를 걸러낸다.

`git_authors`는 안전장치다. `repo_roots` 아래에 CMake `_deps` 같은 외부
저장소가 있으면 남의 커밋이 섞일 수 있다(탐색에서도 제외하지만 이중 방어).
본인 커밋 작성자를 확인하려면:

```bash
git -C <저장소> log --format='%an <%ae>' | sort -u
```

### 4. 동작 확인

```bash
python3 run.py --yesterday --no-llm --dry-run   # 수집만 (LLM·Notion 안 씀)
python3 run.py --yesterday --dry-run            # 요약까지 (Notion 발행 생략)
python3 run.py --yesterday                      # 전체 실행
```

### 5. 자동 실행 등록

```bash
./deploy/install_macos.sh
```

매일 **00:05**에 `--yesterday`로 실행된다. 00:00에 "오늘"을 수집하면 갓 시작한
날이라 항상 비기 때문이다. 날짜를 실행 시점에 계산하므로, 맥이 자다가 아침에
깨어나 실행돼도 대상 날짜는 어제로 유지된다.

```bash
launchctl kickstart -p gui/$(id -u)/ai.predoctor.worklog-daily   # 즉시 1회
tail -f logs/worklog.log                                          # 로그
./deploy/install_macos.sh --uninstall                             # 제거
```

## 사용법

```
python3 run.py [--date YYYY-MM-DD | --yesterday] [--dry-run] [--no-llm]

--date        대상 날짜 (기본: 오늘 KST)
--yesterday   어제. 자정 직후 실행용
--dry-run     Notion 발행 생략, 페이로드만 출력
--no-llm      LLM 호출 없이 자리표시 요약 사용
```

맥을 하루 이상 꺼두어 건너뛴 날은 `--date`로 소급 실행하면 복구된다.

## 발행 이력과 중복 방지

발행할 때마다 `data/YYYY-MM-DD.json`에 요약 내용과 Notion 페이지 ID를 남긴다
(gitignore 대상 — 업무 내용이 들어간다). 이 기록이 두 가지를 처리한다.

**같은 날짜 재실행 → 갱신.** 기록에 페이지 ID가 있으면 새로 만들지 않고 그
페이지의 속성과 본문을 교체한다. 요약이 마음에 안 들어 다시 돌리거나 실패한
날을 소급 실행해도 페이지가 쌓이지 않는다. 페이지를 지웠거나 보관 처리했다면
없는 것으로 보고 새로 만든다(지운 페이지를 되살리지 않는다).

**날짜가 바뀌면 → 직전 발행분과 대조.** 직전 요약(최대 7일 거슬러 탐색)을
프롬프트에 넣어 이미 보고된 항목이 다음 날 그대로 반복되지 않게 한다.
어제 "다음 할 일"이던 것을 오늘 실제로 했다면 오늘의 "한 일"로 올라온다.

`data/`를 지우면 이력이 사라져 같은 날짜라도 새 페이지가 생기고, 직전 대조도
동작하지 않는다.

## 알려진 제약

- **요약 결과가 비면** 그날 `repo_roots` 안에서 커밋·대화가 없었는지 먼저 확인한다.
- **`backend: "cli"`는 그 기기에 Claude Code 로그인이 필요**하다. 계정 사용량을
  소모하며, 한도에 걸린 날은 실패한다(`logs/worklog.err.log` 확인 후 재실행).
- **python.org 배포 Python은 시스템 CA를 읽지 못해** `urllib` HTTPS가 실패한다.
  `publish_notion.py`가 certifi 번들을 쓰도록 처리해 두었다.
- 윈도우 수집기는 아직 없다.
