-- Vow document store — run this once in the Supabase SQL editor.
--
-- Every Vow dataset is one JSON document (budget, guests, seating, contracts,
-- profile, invitations, checklist, timeline, brief, forecast, headcount,
-- activity). The app talks to this table through storage.py using the
-- service-role key, which is server-side only.

create table if not exists vow_documents (
  name        text primary key,
  data        jsonb not null,
  updated_at  timestamptz not null default now()
);

-- Lock the table down: RLS on, and no policies for anon/authenticated —
-- only the service-role key (which bypasses RLS) can read or write.
-- The guest RSVP flow stays safe because guests still go through the app's
-- rate-limited, token-scoped endpoints, never through Supabase directly.
alter table vow_documents enable row level security;
