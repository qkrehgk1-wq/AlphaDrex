$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$model = Join-Path $PSScriptRoot 'models/drex-v1.5-Q8_0/drex-v1.5-Q8_0.gguf'
$runtime = Join-Path $PSScriptRoot 'runtime/llama.cpp/build/bin/llama-server.exe'
if (!(Test-Path -LiteralPath $model) -or !(Test-Path -LiteralPath $runtime)) {
    Write-Host '[AlphaDrex] Drex(이 PC의 AI) 모델 또는 실행 파일이 없습니다.' -ForegroundColor Red
    Write-Host ' - models\drex-v1.5-Q8_0 폴더와 runtime\llama.cpp\build\bin 폴더가 있는지 확인하세요.'
    Write-Host ' - 설치 방법: docs\DREX.md / 앱은 Drex 없이도 매뉴얼 키워드 검색으로 동작합니다.'
    Read-Host '엔터를 누르면 닫힙니다'
    exit 1
}
$env:PATH = "$PSScriptRoot\runtime\w64devkit\bin;" + $env:PATH
& $runtime -m $model --alias drex-v1.5 --embedding --pooling none -np 2 -c 4096 -b 512 -ub 256 --host 127.0.0.1 --port 8000 --threads 4 --log-disable
