-- App-user credentials. Lets a logged-in user change their own password and
-- have it persist (env LEADGEN_USERS is read-only at runtime). Login checks
-- this table first, then falls back to the LEADGEN_USERS env default.

create table if not exists public.app_users (
  username      text primary key,
  password_hash text not null,        -- pbkdf2_sha256$iters$salt_b64$hash_b64
  updated_at    timestamptz not null default now()
);

-- Service-role only — never exposed to anon/authenticated clients.
alter table public.app_users disable row level security;
revoke all on public.app_users from anon, authenticated;
