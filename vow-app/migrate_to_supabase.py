"""One-time migration: push the local data/*.json documents into Supabase.

Steps:
  1. Create a Supabase project and run supabase_schema.sql in its SQL editor.
  2. Put SUPABASE_URL and SUPABASE_SERVICE_KEY into vow-app/.env
     (Project Settings → API: the URL and the service_role key).
  3. From vow-app/:  python migrate_to_supabase.py

Idempotent — re-running upserts the same documents. Local files are left in
place untouched (they simply stop being read once Supabase is configured).
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DATA_DIR = Path(os.environ.get("VOW_DATA_DIR", BASE / "data"))


def main():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in vow-app/.env first "
                 "(Project Settings → API — use the service_role key).")

    os.environ["VOW_STORAGE_BACKEND"] = "supabase"
    import storage
    if storage.backend_name() != "supabase":
        sys.exit("Storage did not come up on the Supabase backend — check the env vars.")

    docs = sorted(DATA_DIR.glob("*.json"))
    if not docs:
        sys.exit(f"No JSON documents found in {DATA_DIR}.")

    pushed = 0
    for path in docs:
        name = path.stem
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"  skipped {path.name}: not valid JSON ({e})")
            continue
        storage.save(name, data)
        print(f"  pushed {name} ({path.stat().st_size:,} bytes)")
        pushed += 1

    print(f"\nDone — {pushed} document(s) now in Supabase table "
          f"'{storage.TABLE}'. The app will read from Supabase on next start.")


if __name__ == "__main__":
    main()
