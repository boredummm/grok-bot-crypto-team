# Register SOL SWARM local recurring tasks in Windows Task Scheduler ($0.00/mo).
# 1. SOLSWARM-DiscoveryCycle: runs every $CycleMinutes (default 5 min) for GMGN sweep + fallback/Grok Bot sync.
# 2. SOLSWARM-SupabaseRetentionPrune: runs every 6 hours to enforce <500 MB Free-tier retention & prevent 7-day pause.
param(
    [ValidateRange(1, 60)]
    [int]$CycleMinutes = 5,
    [ValidateRange(1, 24)]
    [int]$PruneHours = 6
)

$ErrorActionPreference = "Stop"

$CycleScript = Join-Path $PSScriptRoot "run-swarm-cycle.ps1"
$PruneScript = Join-Path $PSScriptRoot "prune-supabase-retention.ps1"

if (-not (Test-Path $CycleScript) -or -not (Test-Path $PruneScript)) {
    Write-Error "Required SOL SWARM scripts missing in $PSScriptRoot."
}

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -Hidden

$CycleAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$CycleScript`""

$CycleTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $CycleMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

Register-ScheduledTask `
    -TaskName "SOLSWARM-DiscoveryCycle" `
    -Description "SOL SWARM local read-only GMGN discovery, Grok Bot coordination, and paper risk cycle ($0/mo)." `
    -Action $CycleAction `
    -Trigger $CycleTrigger `
    -Settings $Settings `
    -Force | Out-Null

$PruneAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PruneScript`""

$PruneTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours $PruneHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

Register-ScheduledTask `
    -TaskName "SOLSWARM-SupabaseRetentionPrune" `
    -Description "SOL SWARM Supabase Free <500 MB retention pruning & 7-day pause keepalive ($0/mo)." `
    -Action $PruneAction `
    -Trigger $PruneTrigger `
    -Settings $Settings `
    -Force | Out-Null

Write-Host "Registered Windows Scheduled Tasks:"
Write-Host "  - SOLSWARM-DiscoveryCycle (every $CycleMinutes minutes)"
Write-Host "  - SOLSWARM-SupabaseRetentionPrune (every $PruneHours hours)"
Write-Host "Zero cloud scheduler costs incurred."
