# integrated 모드 원커맨드 실행 스크립트 (PLAN §1.5 모드 B)
# 사용: .\run_integrated.ps1 [-BindHost 0.0.0.0] [-Port 8000] [-Reload]  (AI/ 디렉터리에서)
#
# Spring Boot가 호출하는 모드다. FastAPI는 저장하지 않고, 목업 프론트(trainee/)도
# 서빙하지 않으며, CORS 미들웨어도 붙지 않는다.
param(
    # PowerShell에서 $Host는 예약된 자동 변수라 -Host 파라미터를 만들 수 없다. 그래서 -BindHost.
    # 기본값은 로컬 전용. Spring이 다른 PC·컨테이너(Docker 등)에서 돈다면 루프백에는
    # 붙을 수 없으므로 -BindHost 0.0.0.0 으로 띄워야 접속된다.
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000,
    # 개발 중 코드 수정 시 자동 재기동
    [switch]$Reload
)
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
}

# .env 상태 점검 — 중단하지 않고 경고만 한다(개발 중 그냥 띄우고 싶은 경우가 있다).
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Warning ".env 파일이 없다. 기본값으로 기동한다 (INTERNAL_API_KEY 미설정 = 인증 비활성)."
    Write-Host "  준비하려면: Copy-Item .env.example .env" -ForegroundColor Yellow
} else {
    # 값 자체는 비밀이므로 절대 출력하지 않는다. 설정 여부만 본다.
    $keyLine = Select-String -Path ".env" -Pattern '^\s*INTERNAL_API_KEY\s*=\s*(\S.*)$' -ErrorAction SilentlyContinue
    if (-not $keyLine) {
        Write-Host ""
        Write-Warning "INTERNAL_API_KEY가 .env에 없거나 비어 있다 -> X-Internal-Key 검증이 비활성화된다."
        Write-Host "  이 상태에서는 키 없이 아무나 /api/v1/* 를 호출할 수 있다. 실제 통합/배포 전에 반드시 채울 것." -ForegroundColor Yellow
    } else {
        Write-Host "INTERNAL_API_KEY: 설정됨 (값은 출력하지 않는다)" -ForegroundColor Green
    }
}

# [핵심] 세션에 남은 APP_MODE를 덮어쓴다.
# 기본값은 이미 integrated지만, run_standalone.ps1이 설정한 $env:APP_MODE="standalone"은
# 스크립트가 끝나도 그 터미널 세션에 그대로 남는다($env:는 프로세스 전역). 그 상태로
# uvicorn을 띄우면 integrated를 의도했는데 standalone으로 떠서 목업 페이지가 서빙된다.
# 명시적으로 덮어써서 그 사고를 막는다.
$env:APP_MODE = "integrated"

Write-Host ""
Write-Host "=== integrated 모드로 기동한다 (호출자: Spring Boot) ===" -ForegroundColor Cyan
Write-Host "  health : http://${BindHost}:${Port}/api/health   -> mode 값이 integrated 여야 정상 (인증 면제)"
Write-Host "  Swagger: http://${BindHost}:${Port}/docs          -> Backend 담당자의 진입점"
Write-Host "  integrated에서는 목업 페이지를 서빙하지 않는다. / 와 /submission.html 이 404인 것이 정상이다."
if ($BindHost -eq "127.0.0.1") {
    Write-Host "  Spring이 다른 PC·컨테이너에서 돈다면: .\run_integrated.ps1 -BindHost 0.0.0.0" -ForegroundColor Yellow
}
Write-Host ""

$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", $Port)
if ($Reload) { $uvicornArgs += "--reload" }
& ".\.venv\Scripts\python.exe" @uvicornArgs
