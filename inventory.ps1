param(
    [switch]$Open,
    [switch]$NoOllama
)

$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$RunScript = Join-Path $ProjectDir "run.py"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment Python not found at $VenvPython"
    exit 1
}

if (-not $NoOllama) {
    $ollamaListening = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
    if (-not $ollamaListening) {
        $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
        $ollamaExe = if ($ollamaCmd) { $ollamaCmd.Source } else { Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
        if (Test-Path $ollamaExe) {
            Write-Host "Starting Ollama (localhost:11434)..." -ForegroundColor Cyan
            Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        } else {
            Write-Warning "Ollama executable not found; local AI scanning will be unavailable."
        }
    } else {
        Write-Host "Ollama already running on port 11434." -ForegroundColor DarkGray
    }
}

function Test-WimsHealth {
    param([string]$Url = "http://localhost:8000/api/v1/health")
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

$wimsListening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($wimsListening -or (Test-WimsHealth)) {
    Write-Host "WIMS Inventory is already running at http://localhost:8000" -ForegroundColor Yellow
    if ($Open) {
        if (-not (Test-WimsHealth)) {
            Write-Host "Waiting for WIMS server to become ready..." -ForegroundColor Cyan
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline -and -not (Test-WimsHealth)) {
                Start-Sleep -Milliseconds 300
            }
        }
        Start-Process "http://localhost:8000"
    }
    exit 0
}

if ($Open) {
    Write-Host "Waiting for WIMS server to start before opening browser..." -ForegroundColor Cyan
    Start-Job -ScriptBlock {
        $healthUrl = "http://localhost:8000/api/v1/health"
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            try {
                $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                if ($resp.StatusCode -eq 200) {
                    Start-Process "http://localhost:8000"
                    break
                }
            } catch {
                Start-Sleep -Milliseconds 300
            }
        }
    } | Out-Null
}

Push-Location $ProjectDir
try {
    & $VenvPython $RunScript
} finally {
    Pop-Location
}
