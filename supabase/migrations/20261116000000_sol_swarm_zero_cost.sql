-- SOL SWARM Zero-Additional-Cost Shared Coordination Schema (Supabase Free Tier)
-- Idempotent migration. Stores ONLY compact agent tasks, structured findings,
-- real seat heartbeats, schema-validated decisions, and dashboard summary snapshot.
-- Historical market observations, klines/candlesticks, and replay datasets stay in local SQLite.

begin;

-- 1. Compact work queue for Grok Bot teammates (SCAN, VET, SOCIAL, CHIEF)
create table if not exists public.swarm_agent_tasks (
  id text primary key,
  cycle_id text not null,
  mode text not null check (mode in ('demo', 'live')),
  seat text not null check (seat in ('SCAN', 'VET', 'SOCIAL', 'CHIEF')),
  mint text not null,
  symbol text not null check (char_length(symbol) <= 32),
  status text not null default 'pending' check (status in ('pending', 'claimed', 'completed', 'expired')),
  priority integer not null default 50,
  evidence_summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  completed_at timestamptz,
  constraint chk_swarm_task_payload_bytes check (octet_length(evidence_summary::text) <= 4096)
);

create index if not exists ix_swarm_tasks_seat_status
  on public.swarm_agent_tasks (mode, seat, status, created_at desc);
create index if not exists ix_swarm_tasks_expires_at
  on public.swarm_agent_tasks (expires_at);

-- 2. Compact structured research findings per seat/token
create table if not exists public.swarm_agent_findings (
  id text primary key,
  task_id text references public.swarm_agent_tasks(id) on delete set null,
  cycle_id text,
  mode text not null check (mode in ('demo', 'live')),
  seat text not null check (seat in ('SCAN', 'VET', 'SOCIAL')),
  mint text not null,
  symbol text not null check (char_length(symbol) <= 32),
  decision_source text not null check (decision_source in ('grok_bot', 'deterministic_fallback', 'optional_jev')),
  verdict text not null check (verdict in ('pass', 'warn', 'kill')),
  confidence numeric(6, 4),
  score integer,
  summary text not null check (char_length(summary) <= 480),
  finding_payload jsonb not null default '{}'::jsonb,
  agent_id text,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  constraint chk_swarm_finding_payload_bytes check (octet_length(finding_payload::text) <= 4096)
);

create index if not exists ix_swarm_findings_mint_seat
  on public.swarm_agent_findings (mode, mint, seat, created_at desc);
create index if not exists ix_swarm_findings_expires_at
  on public.swarm_agent_findings (expires_at);

-- 3. Real seat heartbeats (8 control-room seats)
create table if not exists public.swarm_agent_heartbeats (
  seat text primary key check (seat in ('CHIEF', 'SCAN', 'VET', 'SOCIAL', 'SIZE', 'FILLS', 'RISK', 'CRAWLER')),
  status text not null,
  decision_source text not null check (decision_source in ('grok_bot', 'deterministic_fallback', 'optional_jev', 'deterministic_engine')),
  active_mint text,
  active_symbol text check (active_symbol is null or char_length(active_symbol) <= 32),
  mode text not null default 'demo' check (mode in ('demo', 'live')),
  agent_id text,
  note text check (note is null or char_length(note) <= 280),
  meta jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz not null default now(),
  expires_at timestamptz not null,
  constraint chk_swarm_hb_meta_bytes check (octet_length(meta::text) <= 2048)
);

create index if not exists ix_swarm_heartbeats_expires_at
  on public.swarm_agent_heartbeats (expires_at);

-- 4. Schema-validated structured decisions with explicit decision_source
create table if not exists public.swarm_decisions (
  id text primary key,
  cycle_id text not null,
  mode text not null check (mode in ('demo', 'live')),
  mint text not null,
  symbol text not null check (char_length(symbol) <= 32),
  action text not null check (action in ('propose', 'hold', 'veto')),
  decision_source text not null check (decision_source in ('grok_bot', 'deterministic_fallback', 'optional_jev')),
  confidence numeric(6, 4),
  size_factor numeric(6, 4) not null default 1.0,
  thesis text not null check (char_length(thesis) <= 600),
  reasons jsonb not null default '[]'::jsonb,
  risk_approved boolean,
  risk_reason_codes jsonb not null default '[]'::jsonb,
  paper_order_id text,
  decision_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  constraint chk_swarm_decision_payload_bytes check (octet_length(decision_payload::text) <= 4096)
);

create index if not exists ix_swarm_decisions_mode_mint
  on public.swarm_decisions (mode, mint, created_at desc);
create index if not exists ix_swarm_decisions_expires_at
  on public.swarm_decisions (expires_at);

-- 5. Singleton compact office summary snapshot (<8 KB)
create table if not exists public.swarm_dashboard_state (
  slot text primary key default 'singleton',
  mode text not null check (mode in ('demo', 'live')),
  paper_trading boolean not null default true check (paper_trading is true),
  live_execution_enabled boolean not null default false check (live_execution_enabled is false),
  emergency_stop boolean not null default false,
  equity_usdc text,
  cash_usdc text,
  open_positions integer not null default 0,
  held_symbol text,
  decision_source text not null default 'deterministic_fallback',
  snapshot jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  constraint chk_swarm_dashboard_snapshot_bytes check (octet_length(snapshot::text) <= 8192)
);

-- Enable Row Level Security on all public coordination tables.
-- Backend uses SUPABASE_SERVICE_ROLE_KEY server-side; anon public access is denied.
alter table public.swarm_agent_tasks enable row level security;
alter table public.swarm_agent_findings enable row level security;
alter table public.swarm_agent_heartbeats enable row level security;
alter table public.swarm_decisions enable row level security;
alter table public.swarm_dashboard_state enable row level security;

revoke all on public.swarm_agent_tasks from anon;
revoke all on public.swarm_agent_findings from anon;
revoke all on public.swarm_agent_heartbeats from anon;
revoke all on public.swarm_decisions from anon;
revoke all on public.swarm_dashboard_state from anon;

drop policy if exists swarm_tasks_authenticated_read on public.swarm_agent_tasks;
create policy swarm_tasks_authenticated_read
  on public.swarm_agent_tasks for select to authenticated using (true);

drop policy if exists swarm_findings_authenticated_read on public.swarm_agent_findings;
create policy swarm_findings_authenticated_read
  on public.swarm_agent_findings for select to authenticated using (true);

drop policy if exists swarm_heartbeats_authenticated_read on public.swarm_agent_heartbeats;
create policy swarm_heartbeats_authenticated_read
  on public.swarm_agent_heartbeats for select to authenticated using (true);

drop policy if exists swarm_decisions_authenticated_read on public.swarm_decisions;
create policy swarm_decisions_authenticated_read
  on public.swarm_decisions for select to authenticated using (true);

drop policy if exists swarm_dashboard_authenticated_read on public.swarm_dashboard_state;
create policy swarm_dashboard_authenticated_read
  on public.swarm_dashboard_state for select to authenticated using (true);

-- Storage & row-count telemetry RPC (security invoker; restricted from anon)
create or replace function public.swarm_storage_telemetry()
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_db_bytes bigint;
  v_swarm_bytes bigint;
  v_limit_bytes bigint := 524288000; -- 500 MB Supabase Free database size limit
begin
  v_db_bytes := pg_database_size(current_database());
  v_swarm_bytes := coalesce(pg_total_relation_size('public.swarm_agent_tasks'::regclass), 0)
                 + coalesce(pg_total_relation_size('public.swarm_agent_findings'::regclass), 0)
                 + coalesce(pg_total_relation_size('public.swarm_agent_heartbeats'::regclass), 0)
                 + coalesce(pg_total_relation_size('public.swarm_decisions'::regclass), 0)
                 + coalesce(pg_total_relation_size('public.swarm_dashboard_state'::regclass), 0);

  return jsonb_build_object(
    'database_bytes', v_db_bytes,
    'swarm_tables_bytes', v_swarm_bytes,
    'limit_bytes', v_limit_bytes,
    'usage_pct', round((v_db_bytes::numeric / v_limit_bytes::numeric) * 100.0, 2),
    'headroom_bytes', greatest(v_limit_bytes - v_db_bytes, 0),
    'row_counts', jsonb_build_object(
      'swarm_agent_tasks', (select count(*) from public.swarm_agent_tasks),
      'swarm_agent_findings', (select count(*) from public.swarm_agent_findings),
      'swarm_agent_heartbeats', (select count(*) from public.swarm_agent_heartbeats),
      'swarm_decisions', (select count(*) from public.swarm_decisions),
      'swarm_dashboard_state', (select count(*) from public.swarm_dashboard_state)
    ),
    'checked_at', now()
  );
end;
$$;

revoke all on function public.swarm_storage_telemetry() from public, anon;
grant execute on function public.swarm_storage_telemetry() to authenticated, service_role;

-- Retention & overflow pruning RPC so Supabase Free never approaches 500 MB
create or replace function public.swarm_prune_expired_data(
  p_max_tasks integer default 500,
  p_max_findings integer default 1000,
  p_max_decisions integer default 1000
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_tasks_del integer := 0;
  v_findings_del integer := 0;
  v_hb_del integer := 0;
  v_dec_del integer := 0;
  v_tmp integer := 0;
begin
  update public.swarm_agent_tasks
     set status = 'expired'
   where status in ('pending', 'claimed')
     and expires_at < now();

  delete from public.swarm_agent_tasks
   where expires_at < now() - interval '6 hours';
  get diagnostics v_tasks_del = row_count;

  with overflow as (
    select id from public.swarm_agent_tasks
     order by created_at desc
     offset greatest(p_max_tasks, 50)
  )
  delete from public.swarm_agent_tasks where id in (select id from overflow);
  get diagnostics v_tmp = row_count;
  v_tasks_del := v_tasks_del + v_tmp;

  delete from public.swarm_agent_findings where expires_at < now();
  get diagnostics v_findings_del = row_count;

  with overflow as (
    select id from public.swarm_agent_findings
     order by created_at desc
     offset greatest(p_max_findings, 100)
  )
  delete from public.swarm_agent_findings where id in (select id from overflow);
  get diagnostics v_tmp = row_count;
  v_findings_del := v_findings_del + v_tmp;

  delete from public.swarm_agent_heartbeats where expires_at < now();
  get diagnostics v_hb_del = row_count;

  delete from public.swarm_decisions where expires_at < now();
  get diagnostics v_dec_del = row_count;

  with overflow as (
    select id from public.swarm_decisions
     order by created_at desc
     offset greatest(p_max_decisions, 100)
  )
  delete from public.swarm_decisions where id in (select id from overflow);
  get diagnostics v_tmp = row_count;
  v_dec_del := v_dec_del + v_tmp;

  return jsonb_build_object(
    'pruned_tasks', v_tasks_del,
    'pruned_findings', v_findings_del,
    'pruned_heartbeats', v_hb_del,
    'pruned_decisions', v_dec_del,
    'pruned_at', now()
  );
end;
$$;

revoke all on function public.swarm_prune_expired_data(integer, integer, integer) from public, anon;
grant execute on function public.swarm_prune_expired_data(integer, integer, integer) to service_role;

commit;
