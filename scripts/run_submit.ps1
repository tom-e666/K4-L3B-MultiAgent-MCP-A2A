# One command: fresh run -> validate -> package. Usage (venv active): .\scripts\run_submit.ps1
$ErrorActionPreference = "Stop"
day09 run
if ($LASTEXITCODE -ne 0) { Write-Host "RUN FAILED - try: day09 run --resume"; exit 1 }
day09 validate
if ($LASTEXITCODE -ne 0) { Write-Host "VALIDATE FAILED"; exit 1 }
day09 package --output dist/submission.zip
if ($LASTEXITCODE -ne 0) { Write-Host "PACKAGE FAILED"; exit 1 }
$ctx = Get-Content traces/.run-context.json | ConvertFrom-Json
Write-Host "Run started: $($ctx.run_started_at)  expires: $($ctx.expires_at)"
Write-Host "READY: upload dist/submission.zip"
