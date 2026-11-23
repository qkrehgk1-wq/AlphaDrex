# AlphaDrex

현장 상황 한 줄을 입력하면 **등록된 매뉴얼의 근거 행을 붙여 판단·초안을 만들고, 그 답변을 문서로 저장해 담당자 검토 → 에이전트 수신함 전달 → 접수·회신까지** 한 화면에서 이어 주는 Windows 단일 PC 시제품입니다. 라이너 `.EXE-THON 2026` 예선 제출본(v0.2.0, 2026-11-23 보완)입니다.

- 원칙: 확인되지 않은 사실을 만들지 않고, 근거 없는 초안을 만들지 않으며, 긴급 상황은 AI보다 기관 절차를 먼저 둡니다.
- 시연 데이터: 모두 가상(`가상·시연용`) 또는 공개 재구성 자료입니다. 실제 기관 문서·개인정보는 포함하지 않습니다.

---

## 1. 실행 방법

| 항목 | 내용 |
| --- | --- |
| 필수 | Windows 10/11, Python 3.11 이상 (개발·검증: CPython 3.12) |
| 필수 패키지 | 없음 — 서버·DB·문서 파서는 표준 라이브러리만 사용 |
| 선택 패키지 | `pypdf` (PDF 매뉴얼 등록용). 없으면 PDF 등록만 안내 문구와 함께 거부됩니다 |
| 실행 | 저장소 폴더에서 `start.bat` 더블클릭 (또는 `python -m app.server`) |
| 접속 | 브라우저에서 `http://127.0.0.1:8765` (포트 변경: `ALPHADREX_PORT`) |
| 데이터 위치 | `data/internal.sqlite3`, `data/external.sqlite3` — 내부망·외부망 DB를 물리적으로 분리 |

```powershell
# PowerShell에서 직접 실행하는 경우
cd <AlphaDrex 폴더>
python -m app.server
```

처음 실행하면 가상 매뉴얼 4건과 업무 팩 1개가 자동으로 등록됩니다. 화면 왼쪽 아래 **⚙ 설정 및 관리 → 계정과 기록 → 첫 관리자 계정 만들기**에서 관리자를 만들면 이후 모든 화면 API에 로그인이 필요해집니다(계정을 만들지 않으면 이전처럼 열린 단일 PC 시연 모드).

### 테스트 실행

```powershell
python -m unittest discover -s tests -v
```

API 키·모델·네트워크 없이 10건이 모두 통과합니다(2026-11-23 기준). 구성은 아래와 같습니다.

| 파일 | 검사 내용 |
| --- | --- |
| `tests/test_transport_integrity.py` | 잘린 HTTP 응답 거부, Content-Length 불일치 거부, "장소·시각 미확인"을 완료된 사실로 바꾸지 않음 |
| `tests/test_http_smoke.py` | 실제 HTTP 서버를 띄워 ① 상태·헬스 ② 모델 없는 규칙 경로 ③ 긴급 상황 시 초안 차단 ④ 문서 저장→검토→전달→모의 전송 중복 방지 ⑤ **모의 Liner 응답으로 판단·초안 경로 전체** ⑥ 민감정보가 전송 계층에 도달하지 않음 ⑦ 문서 충돌 시 양쪽 근거 표시·초안 차단 |

⑤번은 Liner 응답을 대역으로 바꿔 끼우는 검사입니다. 실제 키 없이도 "판단 → 근거 선택 → 초안 → 지표 기록" 경로가 끝까지 동작하는지 확인합니다.

---

## 2. 환경 변수 설정

서버는 시작할 때 저장소 폴더의 `env.local` → `.env.local` → `.env` 순서로 읽고, **아래 이름만** 인정합니다(그 밖의 줄은 무시). 이미 설정된 OS 환경 변수는 덮어쓰지 않습니다(`setdefault`). `.env.example`을 복사해 `env.local`로 저장한 뒤 값을 채우세요.

```ini
# env.local (예시)
LINER_API_KEY=발급받은_라이너_키
LINER_MODEL=liner-gpt-router
ALPHADREX_BRAIN=liner        # liner | hybrid | drex
```

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `LINER_API_KEY` | (없음) | Liner Platform API 키. 없으면 Liner 호출은 "API 키 미등록"으로 차단되고 로컬 규칙으로만 답합니다 |
| `LINER_MODEL` | `liner-gpt-router` | Liner Model API에 요청할 모델 이름 |
| `ALPHADREX_BRAIN` | `hybrid` | `liner`=모든 턴을 Liner가 판단, `hybrid`=로컬 Drex 우선·불확실할 때만 Liner, `drex`=로컬만 |
| `ALPHADREX_INTERNAL_ONLY` | `0` | `1`이면 서버가 내부망으로 고정(화면에서 외부망 전환 불가) |
| `ALPHADREX_PORT` | `8765` | 접속 포트 |
| `ALPHADREX_MAX_WAIT_S` | `120` | 한 요청이 AI 응답을 기다리는 전체 상한(초). 넘으면 로컬 규칙으로 답하고 담당자 검토로 표시 |
| `ALPHADREX_DREX_BUDGET_S` | `60` | hybrid에서 Drex를 기다리는 시간. 넘으면 Liner로 넘김 |
| `ALPHADREX_SHADOW_RATE` | `0.1` | Drex 단독 판단 중 Liner도 함께 물어 비교 기록을 남기는 비율 |
| `DREX_URL` / `DREX_MODEL` | `http://127.0.0.1:8000/v1/systemone` / `drex-v1.5` | 로컬 Drex(선택). `127.0.0.1`이 아니면 외부 호출로 취급해 정책 게이트를 통과해야 합니다 |
| `DREX_API_KEY` | (없음) | 로컬이 아닌 Drex 서버를 쓸 때만 사용 |
| `ALPHADREX_AGENT_TOKENS` | (없음) | `{"facilities":{"token":"32자 이상","mode":"internal"}}` 형태의 에이전트 인증. `python -m tools.setup_agents`가 생성 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `ALPHADREX_TELEGRAM_TARGETS` | (없음) | 선택 기능인 Telegram 외부 전송용. 브라우저는 수신 주소를 지정할 수 없고 관리자 환경 변수만 사용합니다 |

키는 코드·ZIP·로그에 넣지 않습니다. `env.local`·`.env*`·`data/`·`models/`·`runtime/`·`*.zip`은 `.gitignore`에 포함되어 있습니다.

**심사용으로 키를 바꿔 실행할 때**: ① `env.local`의 `LINER_API_KEY` 값만 심사용 키로 교체하거나 ② PowerShell에서 `$env:LINER_API_KEY='심사용키'; $env:ALPHADREX_BRAIN='liner'; python -m app.server`로 실행합니다. 그 밖의 코드 수정은 필요 없습니다.

---

## 3. Liner API를 어디에 어떻게 쓰는가

호출은 모두 `app/providers.py`에 모여 있고, `request_json()` 하나를 통과합니다. 이 함수가 **https + `platform.liner.com` 외 주소 거부, 내부망·전송 미허용 시 차단, 리디렉션 차단, 2MB 응답 상한, Content-Length 불일치 거부, 오류 메시지에 URL·키·응답 본문 미포함**을 담당합니다.

| # | 기능 | 코드 | Liner 엔드포인트 | 보내는 것 | 언제 호출되나 |
| --- | --- | --- | --- | --- | --- |
| ① | **판단** (분류·정보 부족·검토 필요·검색 필요·긴급·근거 선택) | `providers.py:301 liner_judge()` | `POST https://platform.liner.com/api/v1/chat/completions` (헤더 `x-api-key`) | `{model, stream:false, max_completion_tokens:600, messages:[판단 지시(system), {situation, categories, manual_lines}]}` | `ALPHADREX_BRAIN=liner`이면 매 턴, `hybrid`이면 Drex가 불확실하거나 느리거나 매뉴얼 행 선택이 필요할 때(+ `SHADOW_RATE` 표본) |
| ② | **초안** (한국어 확인 목록·보고 초안, 근거 인용) | `providers.py:342 draft()` | 같은 `chat/completions` | `{model, max_completion_tokens:1800, messages:[초안 지시 3개(system), {situation, manuals(근거 행), web_unverified}]}` | 근거가 있고, 충돌·긴급·위험 단어·정보 부족이 없고, 사용자가 공개·가상 입력과 외부 전송에 동의했고, 외부망이 켜져 있을 때만 |
| ③ | **검색** (최신 공개 정보) | `providers.py:333 search()` | `POST https://platform.liner.com/api/v1/tools/search/web` | `{query, lang:"ko", country_code:"kr", max_results:5, include_knowledge:false}` | 답변에 "검색 확인"이 뜨고 **사용자가 검색어를 확인한 뒤에만** (`/api/search` → `Engine.search_confirmed`) |

**Liner에 보내는 자료의 범위**

- 판단용 `manual_lines`는 **승인(approved) + 외부 전송 승인(external_approved)** 문서의 인용 가능 행에서 최대 80행만 만듭니다(`Store.liner_candidates`). 내부 자료는 후보에 들어가지 않습니다.
- 초안용 근거도 같은 조건을 다시 검사하며, `providers.sensitive()`(휴대전화·이메일·주민등록번호·주소·계좌·여권·면허·병력 등 패턴)에 걸리면 **전송 전에** 차단합니다. 테스트 ⑥이 이 동작을 고정합니다.
- Liner가 고른 근거는 **행 id로만** 받습니다(`evidence_ids`). 응답에 없는 id는 버리고, Liner가 만들지 않은 문장을 인용으로 승격하지 않습니다.

**실측 기록(제출 영상)**: 2026-11-18 단일 사례에서 판단 1회·초안 1회 실제 호출 성공(요청 `liner-gpt-router` / 응답 `gpt-6-sol`), 근거 1행, 총 8.295초 — `demo/verification.json`. 한 사례의 측정값이며 평균 성능 주장이 아닙니다.

---

## 4. 기존에 만든 부분 / 이번에 새로 발전시킨 부분

`.EXE-THON` 안내에 따라 구분해 적습니다.

| 구분 | 범위 |
| --- | --- |
| **기존 (11/12 이전)** | 자체 코드 없음 — 개발 기록상 빈 폴더에서 시작했습니다. 기존에 만든 결과물을 가져다 발전시킨 경우는 없습니다. |
| **이번에 새로 만든 부분 (11/12~11/19)** | 웹 앱(표준 라이브러리 HTTP 서버 + 브라우저 UI), Liner 연동(판단·초안·검색), 근거 검색·충돌 표시, 문서화·검토·전달·회신, 계정·권한·감사 기록·백업, 가상 매뉴얼과 시험 |
| **이번 보완 (11/23)** | ① 제출 소스에 **API 키 없이 도는 HTTP 종단 테스트 7건** 추가(모의 Liner 경로 포함, 총 10건 통과) ② 초안이 없는 이유를 상황별로 정확히 표시(충돌·긴급·근거 부족·동의 전) ③ `ALPHADREX_MAX_WAIT_S`를 `.env.example`에 명시 ④ README를 실행·환경 변수·Liner 사용 지점·기존/신규 구분 중심으로 재작성 |
| **외부 구성 요소** | Liner Platform API, Python·SQLite·브라우저, 선택 `pypdf`. 로컬 판단을 켤 때만 Nace.AI Drex v1.5 모델과 Nace llama.cpp fork를 사용합니다(모델 가중치는 저장소에 넣지 않음). AI 코딩 도구를 활용했고, Nace.AI와는 독립적으로 개발한 응용 프로그램입니다. |

---

## 5. 화면에서 일어나는 일

1. **상황 입력** → 즉시 키워드 미리보기(저장 안 됨, AI 판단 전).
2. **판단**: 분류 · 정보 부족 · 담당자 검토 필요 · 검색 필요 · 긴급 여부 + **근거 행(문서명·행 번호·버전)**. 근거가 없으면 `근거 부족`으로 표시하고 초안을 만들지 않습니다.
3. **초안**: 근거에 없는 내용은 쓰지 않고, 매뉴얼의 "보고서에는 반드시…" 행은 자동으로 함께 인용합니다.
4. **문서화**: 확인한 사실 / 실제 수행한 조치 / 미확인 사항을 분리한 전달 문서를 만들고 SHA-256 지문으로 버전을 고정합니다(수정본은 이전 버전을 덮어쓰지 않음).
5. **검토 → 전달**: 같은 지문에 대한 검토 기록이 있어야 전달을 준비할 수 있고, 수신자·문서 전체를 확인해야 전송됩니다. 전송은 `prepared → sending` 예약을 먼저 커밋하므로 중복 발송이 생기지 않습니다.
6. **접수·회신**: 에이전트가 자기 토큰으로 접수(claim)하고 회신(reply)하며, 회신은 "담당자 검토 전"으로 별도 저장됩니다.

## 6. 안전장치 요약

- 내부망/외부망 SQLite 분리, 망 사이 문서 자동 복사 없음. 처리 중 망이 바뀌면 요청 거부.
- 외부 전송은 `platform.liner.com`(및 선택적 `console.nace.ai`)만 허용, HTTPS 강제, 리디렉션 차단, 프록시 무시.
- 민감정보 패턴 차단(입력·근거·검색어·전달 문서 모두), 로그에는 본문·키를 남기지 않고 경로·상태·소요시간·역할만 기록.
- 긴급 판정: 규칙과 모델 중 하나라도 위험이면 위험으로 표시하고 초안·외부 전달을 막습니다. 위험 단어를 무시하는 것은 Drex 점수가 0.05 미만일 때뿐이며 그때도 담당자 검토는 유지됩니다.
- 삭제 경로도 검토·전달·회신의 **사실 기록(해시·상태·검토자)은 남깁니다**.

## 7. 알려진 한계

- 구형 바이너리 `.hwp`는 직접 읽지 않습니다(한글에서 HWPX/TXT로 저장 안내). 스캔 PDF는 OCR 미지원.
- 단일 PC SQLite 시제품입니다. 다중 사용자 동시 편집·기관 SSO·전자결재 연동은 범위 밖입니다.
- 로컬 Drex(9B, CPU)는 이 PC에서 응답이 수십 초 걸려 시간 예산을 넘기는 경우가 있습니다. 그래서 제출 영상은 실제 Liner 판단·초안 성공 기록만 담았고, Drex 시연은 별도 검증 과제로 남겼습니다.
- 기대 효과(탐색·보고 시간 절감)는 실제 조직 시범 운영에서 측정할 과제이며, 이 저장소의 수치로 주장하지 않습니다.

---

## English summary

**AlphaDrex** is a single-PC Windows prototype that turns one line of field-situation text into an evidence-cited judgment and Korean report draft, then carries that answer through document versioning, reviewer sign-off, agent inbox hand-off, and acknowledgement — without inventing facts it cannot cite.

- **Run**: Python 3.11+, no third-party packages required (`pypdf` optional for PDF manuals). `start.bat` → `http://127.0.0.1:8765`.
- **Keys**: read at startup from `env.local`, `.env.local`, or `.env` (whitelisted names only); no key is ever stored in code, ZIP, or logs. Judges can run it by replacing `LINER_API_KEY` alone.
- **Where Liner is used** (`app/providers.py`, one guarded transport):
  1. `liner_judge()` → `POST /api/v1/chat/completions` — classifies the situation, flags missing info / review / search / emergency, and picks answering manual lines by id.
  2. `draft()` → same endpoint — writes the Korean checklist and report draft from the selected evidence only.
  3. `search()` → `POST /api/v1/tools/search/web` — runs only after the user confirms the query.
  Only approved, externally-approved manual lines (max 80) are ever sent; PII patterns are blocked before the request is opened.
- **Existing vs new**: nothing was reused from earlier work (started from an empty folder); the app, Liner integration, evidence handling, document/hand-off flow, accounts and tests were all built for this contest (11/12–11/19), with the 11/23 pass adding key-free HTTP tests and clearer blocked-draft reasons.
- **Tests**: `python -m unittest discover -s tests -v` — 10 pass without any key, including a mocked Liner judgment/draft path.
