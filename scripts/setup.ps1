$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.bootstrap\bin\uv.exe')) {
    python -m pip install --target .bootstrap uv==0.13.1
    if ($LASTEXITCODE -ne 0) { throw 'uv installation failed' }
}
& .\.bootstrap\bin\uv.exe sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe scripts\postgres_local.py
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
Write-Output 'Ready. Start with: .\.venv\Scripts\python.exe scripts\dev.py start'
