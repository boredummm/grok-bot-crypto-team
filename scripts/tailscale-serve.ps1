# Share SOL SWARM inside an existing tailnet with Tailscale Serve.
# This script never enables Tailscale Funnel and will not publish the app to the public internet.
$ErrorActionPreference = "Stop"

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Write-Error "Install Tailscale, sign in to your tailnet, then run this script again. https://tailscale.com/download"
}

$funnel = & tailscale funnel status 2>&1 | Out-String
if ($funnel -match "Funnel on") {
    Write-Error "Tailscale Funnel is already on. Disable it with 'tailscale funnel off' before sharing SOL SWARM. This script will not enable Funnel."
}

# HTTPS on the tailnet only, proxying the local paper-trading server.
& tailscale serve --bg --yes --https=443 http://127.0.0.1:8000
Write-Host "SOL SWARM is available to your tailnet through Tailscale Serve."
Write-Host "Funnel was not enabled."
& tailscale serve status
