import { useEffect, useState } from "react";
import { ApiError, CostMonitorData, IntegrationClassification, api, openEventStream } from "../api";

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function badgeClass(cls: IntegrationClassification): string {
  switch (cls) {
    case "free_active":
      return "pill good";
    case "free_unconfigured":
      return "pill info";
    case "unavailable_limitation":
      return "pill warn";
    case "optional_paid_disabled":
      return "pill";
    case "optional_paid_active":
      return "pill warn";
    case "permanently_disabled":
      return "pill bad";
    default:
      return "pill";
  }
}

export function CostMonitor() {
  const [data, setData] = useState<CostMonitorData | null>(null);
  const [notice, setNotice] = useState("");

  const load = () => {
    api<CostMonitorData>("/api/cost-monitor")
      .then(setData)
      .catch(() => undefined);
  };

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 6000);
    const closeSse = openEventStream(load);
    return () => {
      window.clearInterval(timer);
      closeSse();
    };
  }, []);

  async function pruneNow() {
    setNotice("");
    try {
      await api("/api/control/prune-retention", { method: "POST" });
      setNotice("Retention pruning executed on coordination tables. Historical local market data was untouched.");
      load();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Retention prune requires admin token");
    }
  }

  if (!data) {
    return <p className="muted">Loading zero-cost telemetry…</p>;
  }

  const supa = data.supabase;
  const local = data.local_storage;
  const pctUsed = Math.min(100, Math.max(0.2, supa.usage_pct || 0.2));

  return (
    <div data-testid="cost-monitor-view">
      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h2 style={{ margin: 0 }}>Zero-Additional-Cost Monitor &amp; Free-Tier Guardrails</h2>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              Verified required monthly spend: <strong>${data.required_monthly_cost_usd}/mo</strong> · Historical market &amp; kline data stays 100% local on Windows SQLite while Supabase Free holds compact shared coordination only.
            </p>
          </div>
          <div className="pills">
            <span className={data.zero_additional_cost_verified ? "pill good" : "pill warn"} data-testid="zero-cost-verified-pill">
              {data.zero_additional_cost_verified ? "$0.00/MO VERIFIED" : "OPTIONAL PAID ACTIVE"}
            </span>
            <button type="button" className="action" onClick={pruneNow}>
              Prune Expired Retention
            </button>
          </div>
        </div>
        {notice && <p style={{ marginTop: 10 }}>{notice}</p>}
      </div>

      <div className="grid" style={{ marginBottom: 14 }}>
        <article className="card">
          <div className="label">Required Monthly Spend</div>
          <div className="value up" data-testid="required-monthly-cost">${data.required_monthly_cost_usd}</div>
          <div className="muted">Optional paid spend: ${data.optional_paid_spend_usd}</div>
        </article>
        <article className="card">
          <div className="label">Supabase Free Headroom</div>
          <div className="value">{supa.headroom_mb.toFixed(1)} MB</div>
          <div className="muted">of {supa.free_tier_limit_mb} MB limit ({supa.usage_pct.toFixed(3)}% used)</div>
        </article>
        <article className="card">
          <div className="label">Local Historical SQLite</div>
          <div className="value">{formatBytes(local.total_local_bytes)}</div>
          <div className="muted">
            {local.historical_row_counts.market_observations || 0} obs · {local.historical_row_counts.kline_bars || 0} klines (local only)
          </div>
        </article>
        <article className="card">
          <div className="label">Grok Bot / Fallback Decisions</div>
          <div className="value">
            {data.decision_sources.grok_bot || 0} / {data.decision_sources.deterministic_fallback || 0}
          </div>
          <div className="muted">grok_bot vs deterministic_fallback</div>
        </article>
      </div>

      <div className="split" style={{ marginBottom: 14 }}>
        <div className="panel">
          <h2>Supabase Free Coordination Budget (500 MB Cap)</h2>
          <p className="muted">
            Backend mode: <strong className="mono">{supa.mode}</strong> · Org <code>{supa.organization_audit.org_slug}</code> ({supa.organization_audit.plan} plan · {supa.organization_audit.inactivity_pause_days}-day pause mitigated by local scheduler sync).
          </p>
          <div style={{ background: "#111927", borderRadius: 4, height: 12, overflow: "hidden", margin: "10px 0" }}>
            <div
              style={{
                width: `${pctUsed}%`,
                height: "100%",
                background: supa.usage_pct > 80 ? "#ff4d6d" : "#00e676",
              }}
            />
          </div>
          <p className="mono muted">
            DB size: {supa.database_bytes != null ? formatBytes(supa.database_bytes) : "local mirror"} · Swarm tables footprint: {formatBytes(supa.swarm_tables_bytes)} · Max row payload: {supa.retention_policy.max_payload_bytes} B
          </p>
          <table>
            <thead>
              <tr>
                <th>Coordination Table</th>
                <th>Rows</th>
                <th>Retention Policy</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="mono">swarm_agent_tasks</td>
                <td>{supa.row_counts.swarm_agent_tasks ?? 0}</td>
                <td className="muted">TTL {supa.retention_policy.task_ttl_hours}h · cap {supa.retention_policy.max_tasks_rows}</td>
              </tr>
              <tr>
                <td className="mono">swarm_agent_findings</td>
                <td>{supa.row_counts.swarm_agent_findings ?? 0}</td>
                <td className="muted">TTL {supa.retention_policy.finding_ttl_hours}h · cap {supa.retention_policy.max_findings_rows}</td>
              </tr>
              <tr>
                <td className="mono">swarm_agent_heartbeats</td>
                <td>{supa.row_counts.swarm_agent_heartbeats ?? 0}</td>
                <td className="muted">TTL {supa.retention_policy.heartbeat_ttl_hours}h · 8 seats</td>
              </tr>
              <tr>
                <td className="mono">swarm_decisions</td>
                <td>{supa.row_counts.swarm_decisions ?? 0}</td>
                <td className="muted">TTL {supa.retention_policy.decision_ttl_days}d · cap {supa.retention_policy.max_decisions_rows}</td>
              </tr>
              <tr>
                <td className="mono">swarm_dashboard_state</td>
                <td>{supa.row_counts.swarm_dashboard_state ?? 0}</td>
                <td className="muted">Singleton snapshot (&lt;8 KB)</td>
              </tr>
            </tbody>
          </table>
          {supa.last_error && <p className="down" style={{ marginTop: 8 }}>{supa.last_error}</p>}
        </div>

        <div className="panel">
          <h2>Local Windows Storage &amp; GMGN Free Rate Limits</h2>
          <p className="muted">
            SQLite file: <span className="mono">{local.sqlite_path}</span> ({formatBytes(local.sqlite_bytes)} + {formatBytes(local.wal_bytes)} WAL)
          </p>
          <div className="event">
            <strong>Historical Isolation</strong>
            <span className="up">{local.historical_data_isolated_locally ? "STRICTLY LOCAL" : "SHARED"}</span>
            <span className="muted">Klines, observations &amp; replays never enter Supabase</span>
          </div>
          <div className="event">
            <strong>GMGN Read-Only Tier</strong>
            <span>
              {data.gmgn_rate_limits.configured
                ? `Limit ${data.gmgn_rate_limits.limit ?? "5"} · Rem ${data.gmgn_rate_limits.remaining ?? "—"}`
                : "Key not set"}
            </span>
            <span className="muted">Quote mode: {data.gmgn_rate_limits.quote_fallback}</span>
          </div>
          <div className="event">
            <strong>Grok Bot MCP Bridge</strong>
            <span className="mono">{data.grok_bot.mcp_endpoint}</span>
            <span className="muted">
              {data.grok_bot.fresh_seats.length > 0
                ? `Live seats: ${data.grok_bot.fresh_seats.join(", ")}`
                : `Fallback active (${data.grok_bot.fallback_mode})`}
            </span>
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>Integration &amp; Subscription Audit Matrix</h2>
        <table>
          <thead>
            <tr>
              <th>Component</th>
              <th>Classification</th>
              <th>Monthly Cost</th>
              <th>Missing Env</th>
              <th>Verified Capability &amp; Honest Constraint</th>
            </tr>
          </thead>
          <tbody>
            {data.integrations.map((item) => (
              <tr key={item.id} data-testid={`integration-row-${item.id}`}>
                <td><strong>{item.name}</strong></td>
                <td><span className={badgeClass(item.classification)}>{item.classification}</span></td>
                <td className="mono">{item.monthly_cost_usd === "0.00" ? "$0.00" : item.monthly_cost_usd}</td>
                <td className="mono muted">{item.missing_env.length ? item.missing_env.join(", ") : "none"}</td>
                <td className="muted">{item.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
