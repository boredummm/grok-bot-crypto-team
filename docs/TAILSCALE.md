# Tailscale

SOL SWARM stays on the machine. Remote access is Tailscale Serve, which is visible only to your tailnet.

1. Install Tailscale on the Windows machine and sign in to the tailnet you already use.
2. Start the app with `scripts/start.ps1`. For a single port, build the dashboard (`npm run build` in `frontend`) and run the API on port 8000. FastAPI serves `frontend/dist` when `index.html` is present.
3. Run `scripts/tailscale-serve.ps1`.

The script runs `tailscale serve --bg --https=443 http://127.0.0.1:8000`. It does not run `tailscale funnel`. If Funnel is already enabled, the script stops and tells you to turn Funnel off.

The dashboard header shows whether the CLI exists, whether the node is online, whether Serve is configured, and whether Funnel is on. A public Funnel endpoint is treated as a problem, not a feature.

Give Claude or Grok Bot the monitor token and the tailnet HTTPS URL. They can read `/api/monitor/*`. They cannot pause trading, edit settings, or read credentials unless someone separately gives them the admin token. Do not do that.
