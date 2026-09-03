-- infra/supabase/device_links.sql
-- Run once against the project's Supabase Postgres (SQL editor or
-- `supabase db push`). Tracks which user has claimed which discovered
-- LDPlayer instance. FastAPI (src/server/supabase_client.py) is the only
-- writer/reader — it uses the service-role key and enforces ownership
-- itself, so RLS here is defense-in-depth, not the authorization boundary.

create table if not exists device_links (
    user_id uuid not null references auth.users(id) on delete cascade,
    instance_id text not null,
    linked_at timestamptz not null default now(),
    primary key (user_id, instance_id),
    -- One claimant per instance, independent of the composite PK above —
    -- this is what makes "already-linked instances aren't linkable by
    -- others" an atomic DB-level guarantee instead of a check-then-insert
    -- race.
    unique (instance_id)
);

alter table device_links enable row level security;

create policy "users manage their own device links"
    on device_links
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
