"""Per-tenant OAuth2 client-credentials issuance.

Generates and verifies the client_id / client_secret pair a tenant uses
for machine-to-machine API access (POST /api/oauth/token). Entirely
separate from human login. Only the secret's hash is ever persisted —
callers must capture the raw secret returned at issuance/rotation time,
since it cannot be recovered afterward.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from ..models import Tenant

_CLIENT_ID_PREFIX = "cl_"
_CLIENT_SECRET_PREFIX = "cs_"


def generate_client_id() -> str:
    return _CLIENT_ID_PREFIX + secrets.token_hex(12)


def generate_client_secret() -> str:
    return _CLIENT_SECRET_PREFIX + secrets.token_urlsafe(32)


def hash_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_secret(raw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return secrets.compare_digest(hash_secret(raw), hashed)


def issue_credentials(tenant: Tenant) -> str:
    """Assign a fresh client_id + client_secret to `tenant` (mutates it
    in place, caller flushes/commits) and return the raw secret — the
    only time it's ever available in plaintext."""
    tenant.client_id = generate_client_id()
    raw_secret = generate_client_secret()
    tenant.client_secret_hash = hash_secret(raw_secret)
    tenant.client_secret_rotated_at = datetime.now(timezone.utc)
    return raw_secret


def rotate_secret(tenant: Tenant) -> str:
    """Regenerate just the secret, keeping client_id stable (so any
    already-configured integration only needs to update one value)."""
    raw_secret = generate_client_secret()
    tenant.client_secret_hash = hash_secret(raw_secret)
    tenant.client_secret_rotated_at = datetime.now(timezone.utc)
    return raw_secret
