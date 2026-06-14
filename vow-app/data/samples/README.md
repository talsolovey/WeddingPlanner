# Sample data (development only)

These JSON files are the seed data behind the "load an example" buttons on the
budget and guests pages. They exist so you can try Vow with realistic, trap-filled
data without typing anything.

- `budget.json` — example budget items (no `id`s; assigned at load time).
- `guests.json` — example guest settings + households (no `id`s; assigned at load time).

They are **not** the live data. Live data lives one level up in `data/budget.json`,
`data/contracts.json`, and `data/guests.json`.

## Going to production

To ship without any sample data, simply delete this `samples/` folder. The app handles
its absence gracefully: the `/api/*/load-sample` endpoints return a clear "no sample
data available" message instead of erroring, and you can hide the example buttons in the
UI if you like. No code changes required.
