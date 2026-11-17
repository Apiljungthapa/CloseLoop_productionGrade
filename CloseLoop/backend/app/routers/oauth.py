"""OAuth2 client-credentials grant (RFC 6749 §4.4) for machine-to-machine
API access. Entirely separate from human login (email+password, cookie
sessions) — this issues a short-lived bearer token scoped to exactly one
tenant, for scripts/integrations calling the read-only surface under
/api/integration/*. No human user identity is attached to the token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Tenant, TenantStatus
from ..schemas import OAuthTokenOut
from ..auth import CLIENT_TOKEN_EXPIRES_MINUTES, create_client_credentials_token
from ..services.tenant_credentials_service import verify_secret, hash_secret

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# Compared against when client_id doesn't resolve to any tenant, so a
# lookup miss still pays the same hash+compare_digest cost as a real
# tenant with a wrong secret — closes the timing side-channel that would
# otherwise let an attacker distinguish "no such client_id" from "wrong
# secret" by response time alone.
_DUMMY_SECRET_HASH = hash_secret("no-such-client-id-placeholder")


@router.post("/token", response_model=OAuthTokenOut)
async def issue_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if grant_type != "client_credentials":
        raise HTTPException(
            400, "Unsupported grant_type — only 'client_credentials' is supported"
        )

    tenant = (
        await db.execute(select(Tenant).where(Tenant.client_id == client_id))
    ).scalar_one_or_none()
    # Constant-shape failure: whether client_id doesn't exist or the secret
    # doesn't match, the response is identical AND the secret comparison
    # always runs (against a dummy hash when there's no tenant) — don't
    # let this endpoint's response time confirm which client_ids exist.
    secret_hash = tenant.client_secret_hash if tenant else _DUMMY_SECRET_HASH
    secret_ok = verify_secret(client_secret, secret_hash)
    if not tenant or tenant.status != TenantStatus.active or not secret_ok:
        raise HTTPException(401, "Invalid client_id or client_secret")

    token = create_client_credentials_token(tenant)
    return OAuthTokenOut(
        access_token=token,
        expires_in=CLIENT_TOKEN_EXPIRES_MINUTES * 60,
    )
