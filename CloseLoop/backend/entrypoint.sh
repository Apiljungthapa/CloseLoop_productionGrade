#!/bin/sh
set -e

# Wait for Postgres to accept connections (DATABASE_URL format: postgresql+asyncpg://user:pass@host:port/db)
python - <<'PY'
import os, socket, time, urllib.parse as u
raw = os.environ.get("DATABASE_URL", "")
parsed = u.urlparse(raw.replace("postgresql+asyncpg", "postgresql"))
host = parsed.hostname or "db"
port = parsed.port or 5432
for i in range(60):
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.close()
        print(f"Postgres reachable at {host}:{port}")
        break
    except Exception as e:
        print(f"Waiting for Postgres at {host}:{port}... ({i+1}/60) {e}")
        time.sleep(1)
else:
    raise SystemExit("Postgres not reachable after 60s")
PY

# Apply schema migrations (idempotent — no-op if already at head)
echo "Running alembic upgrade head..."
alembic upgrade head

# Seed demo data on first boot. seed.py is idempotent: it skips if data already
# exists. Set SEED_RESET=1 to wipe & re-seed (development only).
if [ "$SEED_ON_BOOT" = "1" ]; then
  echo "SEED_ON_BOOT=1 -> running seed (idempotent; set SEED_RESET=1 to wipe)"
  python seed.py
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
