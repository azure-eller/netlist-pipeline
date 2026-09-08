alter table runs add column placer text not null default 'search' check (placer in ('search', 'claude'));
