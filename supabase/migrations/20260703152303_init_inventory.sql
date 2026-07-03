create table vehicles (
  id uuid primary key default gen_random_uuid(),
  vin text not null unique check (char_length(vin) between 5 and 20),
  make text not null,
  model text not null,
  year int not null check (year between 1950 and 2100),
  price_cents bigint not null check (price_cents >= 0),
  mileage_km int not null default 0 check (mileage_km >= 0),
  status text not null default 'available' check (status in ('available','reserved','sold')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index vehicles_status_idx on vehicles (status);
create index vehicles_created_idx on vehicles (created_at desc);

create table app_events (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('bug_report','client_error')),
  message text not null check (char_length(message) between 1 and 4000),
  context jsonb not null default '{}',
  status text not null default 'new' check (status in ('new','triaged','resolved')),
  created_at timestamptz not null default now()
);
create index app_events_status_idx on app_events (status);
create index app_events_created_idx on app_events (created_at desc);

create or replace function set_updated_at() returns trigger
language plpgsql set search_path = '' as $$ begin new.updated_at = now(); return new; end $$;
create trigger vehicles_updated_at before update on vehicles
  for each row execute function set_updated_at();

-- RLS deny-by-default: no policies => anon key gets nothing via PostgREST.
alter table vehicles enable row level security;
alter table app_events enable row level security;
