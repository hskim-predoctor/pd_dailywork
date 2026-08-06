# worklog_daily

하루치 개발 로그(git 커밋 + Claude Code 대화)를 모아 Claude로 요약하고
Notion 데이터베이스에 자동 발행하는 백그라운드 프로그램.

중앙 서버가 없다. **각 기기가 자기 로그만 수집해 자기 이름으로 직접 발행**하고,
Notion DB의 `작성자` 속성으로 한 사람의 여러 기기 기록을 묶는다.

```
collect_mac.py   수집   git 커밋 + ~/.claude/projects/**/*.jsonl
summarize.py     요약   claude CLI(구독) 또는 anthropic SDK(API)
publish_notion.py 발행  Notion REST (stdlib만 사용)
run.py           오케스트레이터
```

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

여러 사람이 쓸 때는 **DB 하나를 공유**하고 `작성자`로 구분한다.

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
| `backend` | `cli` = Claude Code 구독 사용(기본) / `api` = Anthropic 크레딧 사용 |
| `anthropic_api_key` | `backend: "api"` 일 때만 필요 |
| `notion_token` | 각자 발급한 토큰 |
| `notion_database_id` | 공유 DB의 32자 hex |
| `repo_roots` | 감시할 상위 폴더들. 하위 **전체 깊이**를 재귀 탐색한다 |
| `git_authors` | 본인 커밋만 세기 위한 이름/이메일 목록. 비우면 필터 해제 |
| `scope_claude_to_roots` | `true`(기본)면 `repo_roots` 안 세션의 대화만 수집 |

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
수집 payload는 따로 저장하지 않지만 원본 로그가 남아 있어 재생성이 가능하다.

## 알려진 제약

- **요약 결과가 비면** 그날 `repo_roots` 안에서 커밋·대화가 없었는지 먼저 확인한다.
- **`backend: "cli"`는 그 기기에 Claude Code 로그인이 필요**하다. 계정 사용량을
  소모하며, 한도에 걸린 날은 실패한다(`logs/worklog.err.log` 확인 후 재실행).
- **python.org 배포 Python은 시스템 CA를 읽지 못해** `urllib` HTTPS가 실패한다.
  `publish_notion.py`가 certifi 번들을 쓰도록 처리해 두었다.
- 윈도우 수집기는 아직 없다.
