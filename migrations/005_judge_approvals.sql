-- A served judge is a registered, evaluated, immutable bundle: the models row names the bytes,
-- the approval row names the golden set those bytes passed. Neither row can change; a retrain
-- is a new version.
create table judge_approvals (
  id               bigserial primary key,
  name             text not null,
  version          text not null,
  artifact_sha256  text,                 -- null for the in-process rules judge
  golden_sha       text not null,
  approved_at      timestamptz not null default now(),
  unique nulls not distinct (name, version, artifact_sha256, golden_sha)
);

create function refuse_change() returns trigger language plpgsql as $$
begin
  raise exception '% rows are immutable; register a new version', tg_table_name;
end $$;

create trigger models_immutable before update or delete on models
  for each row execute function refuse_change();
create trigger judge_approvals_immutable before update or delete on judge_approvals
  for each row execute function refuse_change();
