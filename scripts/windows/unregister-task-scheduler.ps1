# Remove SOL SWARM tasks from Windows Task Scheduler.
$ErrorActionPreference = "Continue"

foreach ($TaskName in @("SOLSWARM-DiscoveryCycle", "SOLSWARM-SupabaseRetentionPrune")) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Unregistered scheduled task: $TaskName"
    } else {
        Write-Host "Scheduled task not present: $TaskName"
    }
}
