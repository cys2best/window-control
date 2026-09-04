-- infra/supabase/installs.sql
-- Run once against the project's Supabase Postgres (SQL editor or
-- `supabase db push`). Tracks which account owns which physical PC
-- install, keyed by that install's own Ed25519 public key (generated and
-- persisted locally by src/server/install_identity.py -- the private key
-- never leaves the machine or reaches this table). FastAPI is the only
-- writer/reader, using the service-role key, same trust boundary as every
-- other server-side write in this repo.

create table if not exists installs (
    public_key text primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    updated_at timestamptz not null default now()
);

alter table installs enable row level security;

create policy "users manage their own installs"
    on installs
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
