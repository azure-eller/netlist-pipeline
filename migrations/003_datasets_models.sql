create table datasets (
  id               bigserial primary key,
  name             text not null,
  sampler_version  text not null,
  solver_version   text not null,
  seed             int not null,
  shards           int not null,
  samples_per_shard int not null,
  status           text not null default 'generating'
                   check (status in ('generating', 'ready', 'failed')),
  manifest_key     text,
  n_samples        int,
  created_at       timestamptz not null default now(),
  finished_at      timestamptz
);

create table dataset_shards (
  dataset_id  bigint not null references datasets(id) on delete cascade,
  shard       int not null,
  object_key  text,
  sha256      text,
  n_samples   int,
  seconds     double precision,
  finished_at timestamptz,
  primary key (dataset_id, shard)
);

create table models (
  id           bigserial primary key,
  name         text not null,
  version      text not null,
  dataset_id   bigint references datasets(id),
  artifact_key text not null,
  sha256       text not null,
  metrics      jsonb not null default '{}',
  created_at   timestamptz not null default now(),
  unique (name, version)
);
