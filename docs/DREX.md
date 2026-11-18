# Drex 로컬 연결 조사 및 설치

확인일: 2026-11-12. 사용자가 Hugging Face 공개 가중치 로컬 실행을 요청했습니다.

- 공식 코드: https://github.com/nace-ai/drex-decision-models
- 선택 모델: https://huggingface.co/nace-ai/drex-v1.5-Q8_0
- 고정 revision: `37c2b59a5b8ecb95b19e6bfae2d4041e0de19808`
- 원 모델: https://huggingface.co/nace-ai/drex-v1.5
- 형식: 약 9.53GB Q8_0 GGUF, 9B 기반 선택지 판단 모델. bf16은 약 18GB라 현재 RAM 16GB PC에 부적합합니다.
- 런타임: Nace fork https://github.com/nace-ai/llama.cpp 의 `drex-v2.0` 브랜치. 일반 upstream llama.cpp 대신 systemone 지원 fork를 사용합니다.
- 현재 장치: Windows, RAM 16,866,684,928 bytes, Intel Iris Xe Graphics. NVIDIA CUDA 없음.
- 공식 모델 페이지의 검증 장치는 Ubuntu NVIDIA와 Apple silicon입니다. Windows에서의 동작은 이 프로젝트의 별도 실제 빌드·실행으로 검증합니다. CPU 지원과 Windows 검증 완료를 동일시하지 않습니다.

## 설치 재현

`python tools/download_drex.py`는 고정 revision의 GGUF, README와 LICENSE를 내려받고 Hugging Face LFS SHA256과 비교합니다. 데이터는 models에만 저장합니다. 대형 파일은 Range 요청으로 내려받습니다.

빌드 도구 CMake/Ninja와 GCC가 필요합니다. 이 환경에서는 `python -m pip install cmake ninja`, w64devkit 2.11.0 GCC를 사용했습니다. GCC bin을 PATH에 추가한 뒤 다음을 실행합니다.

```powershell
git clone --depth 1 --branch drex-v2.0 https://github.com/nace-ai/llama.cpp.git runtime/llama.cpp
python -m cmake -S runtime/llama.cpp -B runtime/llama.cpp/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++
python -m cmake --build runtime/llama.cpp/build --target llama-server -j 4
```

`start-drex.ps1`는 127.0.0.1:8000에 CPU 서버를 시작합니다. context는 4096, parallel 슬롯은 2로 메모리 사용을 제한합니다. 큰 상황은 서버의 컨텍스트 한도로 실패할 수 있습니다. 이를 확정 답변으로 바꾸지 않습니다.

## 요청과 응답

`POST /v1/systemone`에 `state`, `questions`를 보냅니다. 업무 분류는 `choice/criteria`, 정보 부족·위험 검토·검색 여부는 `noul`입니다. `answers`의 실제 결과와 모델명, 질문·선택지, 전체 호출 시간을 보존합니다. `evaluation_time_ms`가 있으면 모델 내부 시간으로 별도 기록합니다. 점수를 이 업무의 검증된 정확도로 해석하지 않습니다.

## 라이선스

모델 라이선스는 **Nace.AI Open RAIL-M (Modified)** 입니다. 오픈 가중치이지만 무제한 허용 라이선스가 아닙니다. 모델 폴더의 LICENSE 원문을 함께 보존합니다. 매출·투자 규모, 경쟁 서비스, 재배포와 사용 목적 제한 등이 있으므로 출품·기관 도입 주체의 실제 조건에 맞는 검토가 필요합니다. Drex DLM은 별도의 CC BY-NC 4.0이므로 상용 모델과 혼동하지 않습니다. 모델 가중치·런타임은 소스 ZIP에 포함하지 않습니다.

공식 참조: https://raw.githubusercontent.com/nace-ai/drex-decision-models/main/models/drex-v1.5/README.md

실행 결과는 evaluation/RESULTS.md에 기록합니다. 외부 발표의 183ms는 이 PC의 측정값이 아닙니다.

## 실제 실행 확인

Windows CPU 빌드와 추론 2건 성공. 파일 SHA256은 공식 값과 일치했습니다. 연결 시험 90,545.33ms, 제품 통합 시험의 Drex 왕복 35,343.40ms입니다. 모델 내부 연산 시간 필드는 응답에 없어 별도 연산 시간은 미확인입니다. 두 건의 원시 결과를 evaluation/drex-live.json 및 drex-integrated.json에 보존했습니다. CPU 4스레드, 16GB RAM에서 높은 메모리 사용과 긴 지연이 관찰됐습니다.

## 긴급 판정 질문 (2026-11-14)

질문은 `emergency`(noul)이고 기준값은 0.3입니다. 최종 긴급 판정은 위험 단어 규칙, 생명·안전 표현 규칙, Drex 중 하나라도 해당하면 긴급입니다. 보류 세트 실측 결과는 evaluation/SCENARIO500_RESULTS.md에 있습니다.
