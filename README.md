# KiCad Footprint Agent

데이터시트(PDF 업로드 · 링크 · 부품 번호)에서 핀 정보와 패키지 치수를 읽어 **KiCad 9 회로 기호(.kicad_sym)와 풋프린트(.kicad_mod)** 를 자동으로 만들고, 규칙 검증 · KiCad CLI 파싱 · AI 교차 검토를 거쳐 라이브러리에 저장하는 웹 에이전트입니다.

- 웹 화면: <https://irodmas.com/webfootprintagent/> (또는 로컬 `http://127.0.0.1:8766/`)
- 구조 · 사용법: 화면 위 **사용법 · 파이프라인 구조** 메뉴
- 개발 · 수정 이력: [CHANGELOG.md](CHANGELOG.md) — 화면 「사용법」 아래에도 표시 (고친 뒤 `python tools/sync_changelog.py` 로 반영)
- KiCad · Claude 는 **내 PC 에서** 실행되므로, 웹 화면을 쓰기 전에 PC 에서 로컬 브리지를 먼저 켭니다.

```
웹 화면 (irodmas.com 또는 localhost)
   │  fetch  →  http://127.0.0.1:8766   (허용된 Origin 만, 로컬 Host 만)
   ▼
로컬 브리지  run.py (FastAPI)
   ├─ claude -p --json-schema  (또는 ANTHROPIC_API_KEY) : 데이터시트 검색 · 추출 · 검토
   ├─ 생성기 : .kicad_sym / .kicad_mod (IPC-7351)
   └─ kicad-cli : 파싱 · 렌더링 검증
```

## 실행

```powershell
git clone https://github.com/samcho93/webFootprintAgent
cd webFootprintAgent
start.bat                     # 또는 python run.py  → http://127.0.0.1:8766/
```

| 옵션 | 설명 |
|---|---|
| `--port 9000` / `FP_AGENT_PORT` | 포트 변경 (irodmas.com 화면의 ⚙ 에서 주소도 바꿈) |
| `--no-open` | 브라우저 자동 열기 끔 |
| `FP_AGENT_ORIGINS` | 브리지를 쓸 수 있는 웹 주소 추가 (쉼표 구분) |

irodmas.com 화면에서 쓸 때 브라우저가 "로컬 네트워크의 기기에 접근" 권한을 물으면 허용하세요.

Claude 연결은 둘 중 하나면 됩니다.

- **Claude Code CLI (API 키 불필요)** — `npm i -g @anthropic-ai/claude-code` 후 터미널에서 `claude` 로 한 번 로그인. 서버가 `claude -p --json-schema …` 를 헤드리스로 실행합니다 (PDF 는 Read 도구, 검색은 WebSearch/WebFetch).
- **Anthropic API 키** — `.env` 에 `ANTHROPIC_API_KEY=…` (키가 있으면 API 를 우선 사용).

- KiCad 9 가 설치되어 있으면 `kicad-cli` 를 자동 탐지합니다 (`KICAD_CLI` 로 경로 지정 가능).
- API 키가 없어도 `examples/*.json` 같은 사양 JSON 을 "사양 편집" 탭에 넣으면 생성·검증이 동작합니다.
- 사용법과 파이프라인 구조는 웹 화면 상단 메뉴(**사용법**, **파이프라인 구조**)에서 볼 수 있습니다.

## 폴더 일괄 생성

화면 왼쪽 위 **폴더 일괄** → 데이터시트 폴더 지정(경로 입력 또는 찾아보기) → **PDF 찾기** → 만들 파일 선택 → **일괄 생성 시작**.

- 파일마다 단일 부품과 같은 파이프라인(추출 → 생성 → 검증 → AI 검토 → 저장)을 실행하고, 진행 표에서 파일별 결과를 봅니다.
- 이미 라이브러리에 있는 데이터시트(SHA-1 동일)는 건너뜁니다. 동시 처리 1~3개, 중지 가능.
- 파일 이름은 부품 번호 단서로만 쓰고, 한 데이터시트에서는 대표 부품 · 패키지 하나를 만듭니다.

| API | 설명 |
|---|---|
| `GET /api/fs/dirs?path=` | 폴더 찾아보기 |
| `POST /api/batch/scan` | `{folder, recursive}` → PDF 목록 (라이브러리 저장 여부 포함) |
| `POST /api/batch` | `{folder, files, lib_name, ai_review, auto_save, parallel, skip_existing}` |
| `GET /api/batch/{id}` · `POST /api/batch/{id}/stop` | 진행 상황 · 중지 |

## 파이프라인

```
입력 → ① 자료 확보(업로드/다운로드/웹 검색) → ② 관련 페이지 선별 → ③ 추출(Claude → PartSpec)
     → ④ 생성(심볼 + IPC-7351 풋프린트) → ⑤ 검증(규칙 + kicad-cli) → ⑥ AI 검토 ─┐
                       ↑──────────── 오류 시 수정 사양으로 재생성 (최대 2회) ────┘
     → ⑦ 저장 (library/)
```

## 지원 패키지 유형

| family | 예 |
|---|---|
| gullwing_dual | SOIC, SOP, SSOP, TSSOP, MSOP, SOT-23-5/6 |
| gullwing_quad | QFP, LQFP, TQFP |
| nolead_dual / nolead_quad | DFN/SON, QFN (노출 패드 + 페이스트 분할) |
| chip_2terminal | 0402~2512, SOD-123, SMA/SMB |
| dip / pin_header | DIP, 핀 헤더 |
| bga | BGA/WLCSP (A1 표기, 빈 볼 지원) |
| explicit | 불규칙 패키지 — 데이터시트 권장 랜드 패턴 좌표를 그대로 사용 |

## 테스트

```powershell
python -m pytest tests     # examples/*.json 생성 → 규칙 검증 → kicad-cli 파싱 (kicad-cli 호출당 약 10초)
```

## 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API 키 (없으면 Claude CLI 사용) |
| `FP_AGENT_BACKEND` | `auto` | `api` / `cli` 강제 |
| `CLAUDE_CLI` | 자동 탐지 | claude 실행 파일 경로 |
| `FP_AGENT_CLI_TIMEOUT` | `1500` | CLI 1회 호출 제한 시간(초) |
| `FP_AGENT_MODEL` | `claude-opus-5-5` | 사용 모델 |
| `FP_AGENT_LIB` | `./library` | 라이브러리 저장 폴더 |
| `FP_AGENT_REVIEW_ROUNDS` | `2` | AI 검토-수정 최대 반복 |
| `KICAD_CLI` | 자동 탐지 | kicad-cli 경로 |

> 자동 생성 결과는 실제 보드에 쓰기 전에 데이터시트와 대조해 확인하세요 (1번 핀 위치, 패드 번호 방향, EP 번호, 극성).
