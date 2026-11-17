import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .config import settings
from .database import get_db
from .models import User, Role, RefreshToken, PasswordResetToken, Tenant, TenantStatus

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_COOKIE = "cl_access"
REFRESH_COOKIE = "cl_refresh"


def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_ctx.verify(pw, hashed)


# bcrypt is deliberately slow (~100-300ms of CPU per call). Calling it directly
# from an async request handler blocks the event loop for that whole time, so
# concurrent logins/sign-ups serialise behind each other. The web layer must
# use these thread-pooled wrappers; the bare sync versions above are kept for
# scripts (seed.py) that run outside the event loop.
async def hash_password_async(pw: str) -> str:
    return await run_in_threadpool(hash_password, pw)


async def verify_password_async(pw: str, hashed: str) -> bool:
    return await run_in_threadpool(verify_password, pw, hashed)


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_EXPIRES_MINUTES
    )
    payload = {
        "sub": user.id,
        "tenantId": user.tenant_id,
        "role": user.role.value,
        "email": user.email,
        "name": user.name,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


CLIENT_TOKEN_EXPIRES_MINUTES = 60


def create_client_credentials_token(tenant: Tenant) -> str:
    """Mint a short-lived JWT for a tenant's OAuth2 client-credentials grant
    (machine-to-machine API access — POST /api/oauth/token). Carries no
    human user identity; `sub` names the client_id so it's traceable in
    logs, and `type` distinguishes it from a user session token so it can
    never be replayed against the human-login-only endpoints."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=CLIENT_TOKEN_EXPIRES_MINUTES)
    payload = {
        "sub": f"client:{tenant.client_id}",
        "tenantId": tenant.id,
        "type": "client_credentials",
        "scope": "integration",
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


# Declared as a proper Security scheme (rather than manual header parsing)
# so FastAPI registers it in the OpenAPI spec — this is what makes the
# "Authorize" button show up in /docs. Paste the `access_token` returned by
# POST /api/auth/login there to test endpoints as that user; auto_error=False
# keeps the browser app's cookie-only flow working when no header is sent.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the access_token from POST /api/auth/login (no need to type 'Bearer').",
)


async def get_current_client_tenant(
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Tenant:
    """Dependency for the integration API — validates a client-credentials
    bearer token and returns the owning Tenant. Re-checks the tenant is
    still active on every call rather than trusting the token's claims,
    so suspending or deleting a tenant immediately revokes its API access
    even for tokens issued before the change and not yet expired."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "client_credentials":
        raise HTTPException(status_code=401, detail="Not a client-credentials token")
    tenant_id = payload.get("tenantId")
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not tenant or tenant.status != TenantStatus.active:
        raise HTTPException(status_code=401, detail="Tenant not found or inactive")
    return tenant


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def _hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_refresh_token(db: AsyncSession, user: User) -> str:
    """Mint a new opaque refresh token and persist only its hash, so the
    token itself never needs to be readable from the database."""
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_refresh(raw),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.JWT_REFRESH_EXPIRES_DAYS),
        )
    )
    await db.flush()
    return raw


async def rotate_refresh_token(db: AsyncSession, raw: str) -> tuple[str, User]:
    """Validate + revoke the presented refresh token and issue a replacement.
    Rotating on every use means a stolen-and-replayed refresh token stops
    working the moment the legitimate client uses it again."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(raw))
    )
    row = result.scalar_one_or_none()
    if not row or row.revoked_at is not None or row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    row.revoked_at = datetime.now(timezone.utc)

    user = (
        await db.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_raw = await issue_refresh_token(db, user)
    return new_raw, user


async def revoke_refresh_token(db: AsyncSession, raw: str) -> None:
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(raw))
    )
    row = result.scalar_one_or_none()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: str) -> None:
    """Kill every other active session for a user — used on password reset
    so a stolen-but-now-useless password can't keep an existing session
    alive."""
    rows = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.revoked_at = now


def _hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_reset_token(db: AsyncSession, user: User) -> str:
    """Mint a "forgot password" token and persist only its hash — mirrors
    issue_refresh_token. The raw value is only ever readable in the
    emailed link."""
    raw = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_reset_token(raw),
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=settings.RESET_TOKEN_EXPIRES_MINUTES),
        )
    )
    await db.flush()
    return raw


async def consume_reset_token(db: AsyncSession, raw: str) -> User:
    """Validate a presented reset token (exists, unused, unexpired) and
    mark it used. Deliberately one generic error for every failure mode —
    invalid, already-used, and expired all look identical to the caller,
    so a guess can't be used to probe which case it hit."""
    row = (
        await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == _hash_reset_token(raw)
            )
        )
    ).scalar_one_or_none()
    if (
        not row
        or row.used_at is not None
        or row.expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=400, detail="This reset link is invalid or has expired."
        )
    row.used_at = datetime.now(timezone.utc)
    user = (
        await db.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=400, detail="This reset link is invalid or has expired."
        )
    return user


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    common = dict(
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        domain=settings.COOKIE_DOMAIN,
    )
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.JWT_EXPIRES_MINUTES * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.JWT_REFRESH_EXPIRES_DAYS * 24 * 3600,
        path="/api/auth",
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=settings.COOKIE_DOMAIN)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth", domain=settings.COOKIE_DOMAIN)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token and credentials:
        # Bearer header fallback keeps /docs "Authorize" and non-browser API
        # clients working even though the browser app relies on cookies.
        token = credentials.credentials
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    payload = decode_token(token)
    if payload.get("type") == "client_credentials":
        raise HTTPException(
            status_code=401,
            detail="Client-credentials tokens cannot access user-session routes",
        )
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_roles(*allowed: Role):
    allowed_names = ", ".join(r.value for r in allowed)

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"This action requires one of: {allowed_names}. Your role: {user.role.value}.",
            )
        return user

    return _dep


async def require_tenant(user: User = Depends(get_current_user)) -> User:
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context required")
    return user
