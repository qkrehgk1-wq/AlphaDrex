# AlphaDrex

현장의 가상 업무 질문에 등록 매뉴얼의 근거를 표시하고, 답변을 문서로 저장해 담당자 검토와 에이전트 수신함 접수까지 이어주는 Windows 단일 PC 시제품입니다.

Python 3.11 이상에서 `start.bat`를 실행하고 브라우저에서 http://127.0.0.1:8765 를 엽니다. 기본 설정은 내부망입니다. Liner 시연을 재현하려면 로컬 `env.local`에 `LINER_API_KEY`와 `ALPHADREX_BRAIN=liner`를 설정하고 서버를 재시작합니다. 설정에서 외부망과 전송 허용을 선택하고, 질문마다 공개·가상 입력임을 확인해야 Liner를 호출합니다.

[실제 Liner 시연 영상](demo/AlphaDrex-Liner-live-demo.mp4)에서 판단 1회와 초안 생성 1회, 매뉴얼 근거 확인, 문서화 및 시험 커넥터 회신을 볼 수 있습니다. 영상 길이는 39.28초입니다. [시연 검증 기록](demo/verification.json)

로컬 Drex 설치 조건은 [Drex 안내](docs/DREX.md), 수신함 연결 방법은 [에이전트 연결 안내](docs/AGENT_HANDOFF.md)를 참고하세요. 모델 가중치는 별도로 준비합니다. 이 프로젝트는 Nace.AI와 독립적으로 개발했습니다.

포함된 전송 처리 회귀검사는 `python -m unittest discover -s tests`로 실행합니다.
