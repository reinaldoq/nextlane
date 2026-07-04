-- Mission Control: a live in-app dashboard of rails agent runs (design spec
-- Sec6/Sec12 -- the one sanctioned post-Phase-1-freeze app addition). The
-- runner (rails/mission_control.py) writes here directly via PostgREST
-- against the HOSTED Supabase project (service-role key); the deployed API
-- (api/_lib/runs.py) is the sole READ path, over the pooled DATABASE_URL,
-- exactly like every other table -- see AGENTS.md's RLS deny-by-default
-- convention.
create table agent_runs (
  id uuid primary key default gen_random_uuid(),
  ts_iso timestamptz not null default now(),
  task_kind text not null,
  task_summary text not null,
  engine text not null,
  reviewer_engine text,
  status text not null default 'running' check (
    status in (
      'running', 'pr_opened', 'gate_failed', 'no_changes', 'timeout', 'error', 'completed_no_pr'
    )
  ),
  gate_ok boolean,
  retries int not null default 0,
  review_verdict text,
  cost_usd numeric,
  pr_url text,
  worktree_branch text,
  finished_at timestamptz
);
create index agent_runs_ts_idx on agent_runs (ts_iso desc);

create table run_steps (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  seq int not null,
  phase text not null, -- worktree | builder | gate | review | retro | pr | ...
  status text not null default 'started' check (status in ('started', 'ok', 'failed')),
  detail text,
  at timestamptz not null default now()
);
create index run_steps_run_idx on run_steps (run_id, seq);

-- RLS deny-by-default: no policies => anon/authenticated keys get nothing via
-- PostgREST. The runner writes with the service-role key (bypasses RLS,
-- mirrors rails/events.py's app_events pattern); the deployed API reads via
-- the pooled superuser DATABASE_URL (also bypasses RLS, same as vehicles).
alter table agent_runs enable row level security;
alter table run_steps enable row level security;
