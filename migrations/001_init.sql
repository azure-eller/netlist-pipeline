create table designs (
  id          bigserial primary key,
  filename    text not null,
  sha256      text not null unique,
  object_key  text not null,
  has_board   boolean not null default false,
  created_at  timestamptz not null default now()
);

create table runs (
  id          bigserial primary key,
  design_id   bigint not null references designs(id),
  mode        text not null check (mode in ('judge', 'generate')),
  seeds       int not null default 1,
  oracle      boolean not null default false,
  status      text not null default 'queued'
              check (status in ('queued', 'running', 'done', 'failed', 'failed_verification')),
  error       text,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);

create table jobs (
  id          bigserial primary key,
  kind        text not null,
  payload     jsonb not null,
  status      text not null default 'queued'
              check (status in ('queued', 'running', 'done', 'failed')),
  attempts    int not null default 0,
  error       text,
  created_at  timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
create index jobs_queued on jobs (id) where status = 'queued';

create table stages (
  id           bigserial primary key,
  run_id       bigint not null references runs(id),
  name         text not null,
  status       text not null check (status in ('running', 'done', 'failed')),
  tool         text,
  tool_version text,
  input_hash   text,
  output_hash  text,
  error        text,
  details      jsonb not null default '{}',
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);
create index stages_run on stages (run_id, id);

create table components (
  id         bigserial primary key,
  design_id  bigint not null references designs(id) on delete cascade,
  ref        text not null,
  value      text,
  footprint  text,
  unique (design_id, ref)
);

create table nets (
  id         bigserial primary key,
  design_id  bigint not null references designs(id) on delete cascade,
  code       int not null,
  name       text not null,
  unique (design_id, name)
);

create table net_nodes (
  id      bigserial primary key,
  net_id  bigint not null references nets(id) on delete cascade,
  ref     text not null,
  pin     text not null
);
create index net_nodes_net on net_nodes (net_id);

create table constraints (
  id      bigserial primary key,
  run_id  bigint not null references runs(id),
  source  jsonb not null,
  body    jsonb not null
);

create table candidates (
  id          bigserial primary key,
  run_id      bigint not null references runs(id),
  seed        int not null,
  board_key   text not null,
  proxy_cost  double precision,
  score       double precision,
  metrics     jsonb,
  chosen      boolean not null default false
);
create index candidates_run on candidates (run_id);

create table verifications (
  id             bigserial primary key,
  run_id         bigint not null references runs(id),
  candidate_id   bigint not null references candidates(id),
  passed         boolean not null,
  drc            jsonb not null,
  netlist_match  boolean not null,
  unrouted       int not null,
  in_bounds      boolean not null
);

create table artifacts (
  id          bigserial primary key,
  run_id      bigint not null references runs(id),
  name        text not null,
  object_key  text not null,
  sha256      text not null,
  bytes       bigint not null,
  unique (run_id, name)
);
