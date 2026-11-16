# Prune expired coordination rows in Supabase Free + local SQLite and checkpoint local WAL.
# Keeps Supabase Free usage well under 500 MB AND generates regular DB activity so the
# Supabase Free 7-day inactivity pause is prevented automatically.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "task-scheduler-prune.log"

function Write-PruneLog([string]$Message) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    "$stamp  $Message" | Out-File -FilePath $LogFile -Append -Encoding utf8
}

$EnvPath = Join-Path $Root ".env"
if (-not (Test-Path $EnvPath)) {
    Write-PruneLog "SKIP: .env not found."
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

try {
    $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/health" -TimeoutSec 3
    if ($health.status -eq "ok" -and $AdminToken) {
        $headers = @{ Authorization = "Bearer $AdminToken" }
        $pruned = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/control/prune-retention" -Headers $headers -TimeoutSec 20
        $cost = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/cost-monitor" -Headers $headers -TimeoutSec 10
        Write-PruneLog ("OK [http] pruned_at={0} supa_mode={1} headroom_mb={2}" -f `
            $pruned.pruned_at, $cost.supabase.mode, $cost.supabase.headroom_mb)
        exit 0
    }
} catch {
    # Fall through to standalone Python prune
}

$PyExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) {
    Write-PruneLog "ERROR: .venv not found at $PyExe."
    exit 1
}

Push-Location (Join-Path $Root "backend")
try {
    $out = & $PyExe -c "import asyncio, json; from sqlalchemy import text; from app.config import Settings; from app.db import init_engine, session_scope; from app.swarm import Swarm; s=Settings(); init_engine(s); sw=Swarm(s); sw.bootstrap(); res=asyncio.run(sw.coord.prune_expired()); asyncio.run(sw.aclose());
with session_scope() as sess: sess.execute(text('PRAGMA wal_checkpoint(TRUNCATE)'));
print(json.dumps(res))"
    Write-PruneLog "OK [headless] $out"
} finally {
    Pop-Location
}
