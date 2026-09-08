-- Data factory step 1 (docs/FACTORY.md): boards we window, and the windows with their labels.
create table boards (
  id          bigserial primary key,
  source      text not null,          -- 'fixture' | 'golden' | 'run' | 'github' ...
  path        text not null,          -- where it came from, for humans
  sha256      text not null unique,   -- of the .kicad_pcb bytes
  object_key  text not null,          -- boards/<sha256>.kicad_pcb
  family      text not null,          -- split unit: boards of one origin stay on one side
  n_layers    int not null,
  n_nets      int not null,           -- nets with copper
  stackup     jsonb not null,
  created_at  timestamptz not null default now()
);

create table windows (
  id              bigserial primary key,
  board_id        bigint not null references boards(id) on delete cascade,
  net             text not null,
  radius_mm       real not null,
  window_version  text not null,
  geometry_hash   text not null unique,  -- sha256 of the canonical local geometry
  object_key      text not null,         -- windows/<geometry_hash>.json: window, cuts, labels
  n_conductors    int not null,          -- segments + vias + pads in the box
  n_cuts          int not null,
  labels          jsonb not null,        -- per-cut params and z0 aggregates
  solver_version  text not null,
  seconds         real not null,
  created_at      timestamptz not null default now()
);
create index on windows (board_id);
