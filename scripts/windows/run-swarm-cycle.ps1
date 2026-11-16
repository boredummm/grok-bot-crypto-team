# Run one recurring SOL SWARM discovery, coordination, and paper-risk cycle on Windows.
# Designed for Windows Task Scheduler ($0.00/mo cloud scheduler replacement).
# Never logs tokens, never enables Tailscale Funnel, never submits live swaps.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "task-scheduler-cycle.log"

function Write-CycleLog([string]$Message) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    "$stamp  $Message" | Out-File -FilePath $LogFile -Append -Encoding utf8
}

$EnvPath = Join-Path $Root ".env"
if (-not (Test-Path $EnvPath)) {
    Write-CycleLog "SKIP: .env not found at $EnvPath. Run scripts\start.ps1 once first."
    exit 0
}

$EnvMap = @{}
foreach ($line in Get-Content $EnvPath) {
    $trimmed = $line.Trim()
    if ($trimmed -and -not $trimmed.StartsWith("#") -and $trimmed.Contains("=")) {
        $parts = $trimmed.Split("=", 2)
        $EnvMap[$parts[0].Trim()] = $parts[1].Trim()
    }
}

$Port = if ($EnvMap.ContainsKey("SOLSWARM_PORT") -and $EnvMap["SOLSWARM_PORT"]) { $EnvMap["SOLSWARM_PORT"] } else { "8000" }
$AdminToken = $EnvMap["SOLSWARM_ADMIN_TOKEN"]
$BaseUrl = "http://127.0.0.1:$Port"

# Safety check: refuse cycle if Tailscale Funnel was turned on externally.
if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    $funnelOut = & tailscale funnel status 2>&1 | Out-String
    if ($funnelOut -match "Funnel on") {
        Write-CycleLog "BLOCKED: Tailscale Funnel is active. Disable with 'tailscale funnel off'."
        exit 1
    }
}

$ServerUp = $false
try {
    $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/health" -TimeoutSec 3
    if ($health.status -eq "ok" -and $health.live_execution_enabled -eq $false) {
        $ServerUp = $true
    }
} catch {
    $ServerUp = $false
}

if ($ServerUp -and $AdminToken) {
    $headers = @{ Authorization = "Bearer $AdminToken" }
    $res = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/control/cycle" -Headers $headers -TimeoutSec 30
    $cost = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/cost-monitor" -Headers $headers -TimeoutSec 10
    Write-CycleLog ("OK [http] mode={0} candidates={1} source={2} req_cost=${3}/mo supa_headroom={4}MB" -f `
        $res.mode, $res.candidates, $res.decision_source, $cost.required_monthly_cost_usd, $cost.supabase.headroom_mb)
    exit 0
}

# Headless fallback when FastAPI server is not running: execute one local cycle directly via .venv Python.
$PyExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) {
    Write-CycleLog "ERROR: .venv not found at $PyExe."
    exit 1
}

Push-Location (Join-Path $Root "backend")
try {
    $out = & $PyExe -c "import asyncio, json; from app.config import Settings; from app.db import init_engine; from app.swarm import Swarm; s=Settings(); s.require_tokens(); init_engine(s); sw=Swarm(s); sw.bootstrap(); r=asyncio.run(sw.run_cycle()); asyncio.run(sw.aclose()); print(json.dumps(r))"
    Write-CycleLog "OK [headless] $out"
} finally {
    Pop-Location
}
