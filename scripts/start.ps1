# Start SOL SWARM on Windows. Paper trading only. Binds to localhost.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "$Root\data" | Out-Null

if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $path = "$Root\.env"
    $lines = Get-Content $path
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -like "$Name=*") {
            $found = $true
            $current = $line.Substring($Name.Length + 1)
            if ([string]::IsNullOrWhiteSpace($current)) { "$Name=$Value" } else { $line }
        } else {
            $line
        }
    }
    if (-not $found) { $updated += "$Name=$Value" }
    Set-Content -Path $path -Value $updated
}

Set-EnvValue "SOLSWARM_ADMIN_TOKEN" ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
Set-EnvValue "SOLSWARM_MONITOR_TOKEN" ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
Write-Host "Admin and monitor tokens are in .env. Paste one into the dashboard. They are not printed here."

if (-not (Test-Path "$Root\.venv")) {
    py -3.12 -m venv "$Root\.venv"
}
& "$Root\.venv\Scripts\python.exe" -m pip install -r "$Root\backend\requirements.txt"
Push-Location "$Root\frontend"
if (-not (Test-Path "node_modules")) { npm install }
Pop-Location

$backend = Start-Process -FilePath "$Root\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:create_app","--factory","--host","127.0.0.1","--port","8000" -WorkingDirectory "$Root\backend" -PassThru
$frontend = Start-Process -FilePath "npm" -ArgumentList "run","dev","--","--host","127.0.0.1","--port","5173" -WorkingDirectory "$Root\frontend" -PassThru
Write-Host "API http://127.0.0.1:8000  Dashboard http://127.0.0.1:5173"
Write-Host "Backend pid $($backend.Id)  Frontend pid $($frontend.Id)"
Write-Host "For tailnet access, build the UI, then run scripts\tailscale-serve.ps1 against the API port. Funnel is not used."
