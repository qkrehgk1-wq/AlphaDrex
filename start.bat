@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=python
if exist "%~dp0python\python.exe" set PY="%~dp0python\python.exe"
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo [AlphaDrex] Python을 찾을 수 없습니다.
  echo  - 오프라인 꾸러미라면 python 폴더가 함께 복사되었는지 확인하세요.
  echo  - 아니면 관리자에게 Python 3.11 이상 설치를 요청하세요.
  pause
  exit /b 1
)
echo [AlphaDrex] 서버를 시작합니다. 이 창을 닫으면 앱이 꺼집니다.
%PY% -m app.server
pause
