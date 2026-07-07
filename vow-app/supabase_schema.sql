-- Vow document store — run this once in the Supabase SQL editor.
--
-- Every Vow dataset is one JSON document (budget, guests, seating, contracts,
-- profile, invitations, checklist, timeline, brief, forecast, headcount,
-- activity), owned by one couple (couple_id = the Supabase Auth user id;
-- 'default' holds pre-auth data). The app talks to this table through
-- storage.py using the service-role key, which is server-side only.

create table if not exists vow_documents (
  couple_id   text not null default 'default',
  name        text not null,
  data        jsonb not null,
  updated_at  timestamptz not null default now(),
  primary key (couple_id, name)
);

-- Lock the table down: RLS on, and no policies for anon/authenticated —
-- only the service-role key (which bypasses RLS) can read or write.
-- Auth users exist in auth.users; they still never touch this table directly:
-- every read/write goes through the app's session-gated endpoints.
alter table vow_documents enable row level security;

-- ============================================================
-- MIGRATION — run this block instead if the table already exists
-- from the pre-auth schema (name as sole primary key):
--
--   alter table vow_documents
--     add column if not exists couple_id text not null default 'default';
--   alter table vow_documents drop constraint vow_documents_pkey;
--   alter table vow_documents add primary key (couple_id, name);
--
-- Existing rows land under couple_id 'default'. After you sign up in the
-- app, claim them for your account (your user id is in Supabase →
-- Authentication → Users):
--
--   update vow_documents set couple_id = '<your-user-uuid>'
--     where couple_id = 'default';
-- ============================================================
