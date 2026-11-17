from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import User, Tenant, TenantStatus, PasswordResetToken
from ..schemas import LoginIn, LoginOut, UserOut, MeUpdateIn, ForgotPasswordIn, ResetPasswordIn
from ..auth import (
    verify_password_async,
    create_access_token,
    get_current_user,
    hash_password_async,
    issue_refresh_token,
    rotate_refresh_token,
    revoke_refresh_token,
    revoke_all_refresh_tokens,
    issue_reset_token,
    consume_reset_token,
    set_auth_cookies,
    clear_auth_cookies,
    REFRESH_COOKIE,
)
from ..services.audit_service import log_action
from ..services.email_service import send_password_reset_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Identical response whether or not the email is registered — this
# endpoint must never let a caller distinguish "sent" from "no such
# account" by status code, body, or timing-visible behavior difference.
FORGOT_PASSWORD_RESPONSE = {
    "message": "If an account exists for that email, a password reset link has been sent."
}


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn, response: Response, db: AsyncSession = Depends(get_db)):
    """Log in and receive both the session cookies (used by the web app)
    and the raw `access_token` in the response body. For Swagger/API
    testing: copy `access_token` into the "Authorize" button above — the
    role on that token (Platform Owner, Super Admin, Group Manager, Team
    Lead, Team Member) determines exactly which endpoints will accept it;
    everything else responds 403."""
    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if user and user.locked_until and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(
            status_code=423,
            detail=f"Too many failed attempts. Try again in {remaining} minute(s).",
        )

    if not user or not await verify_password_async(payload.password, user.password_hash):
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.tenant_id:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
        ).scalar_one_or_none()
        if tenant and tenant.status == TenantStatus.suspended:
            raise HTTPException(
                status_code=403,
                detail="Your organisation has been suspended. Contact the Platform Owner.",
            )

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    await db.flush()

    access_token = create_access_token(user)
    refresh_token = await issue_refresh_token(db, user)

    await log_action(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="USER_LOGIN",
        entity_type="User",
        entity_id=user.id,
    )

    await db.commit()
    await db.refresh(user)

    set_auth_cookies(response, access_token, refresh_token)
    return LoginOut(**UserOut.model_validate(user).model_dump(), access_token=access_token)


@router.post("/refresh", response_model=LoginOut)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="No refresh token")

    new_refresh, user = await rotate_refresh_token(db, raw)
    access_token = create_access_token(user)
    await db.commit()

    set_auth_cookies(response, access_token, new_refresh)
    return LoginOut(**UserOut.model_validate(user).model_dump(), access_token=access_token)


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        await revoke_refresh_token(db, raw)
        await db.commit()
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: MeUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service profile update — any authenticated user (any role, any
    tenant, including PLATFORM_OWNER) may edit their own name / email /
    password. Changing the password requires the current password."""
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        user.name = name

    if payload.email is not None and payload.email != user.email:
        existing = (
            await db.execute(
                select(User).where(User.email == payload.email, User.id != user.id)
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(409, "Email already in use")
        user.email = payload.email

    if payload.new_password:
        if not payload.current_password or not await verify_password_async(
            payload.current_password, user.password_hash
        ):
            raise HTTPException(400, "Current password is incorrect")
        user.password_hash = await hash_password_async(payload.new_password)

    await log_action(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="USER_PROFILE_UPDATED",
        entity_type="User",
        entity_id=user.id,
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset link by email. Always returns the same
    generic message — whether a matching account exists is never
    observable from this endpoint's response."""
    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    if user:
        # Anti-spam: skip minting (and emailing) another token if one was
        # already issued to this user in the last minute — repeated clicks
        # on "resend" don't flood their inbox or this Gmail account.
        recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        recent = (
            await db.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.created_at > recent_cutoff,
                )
            )
        ).scalar_one_or_none()
        if not recent:
            raw_token = await issue_reset_token(db, user)
            await log_action(
                db,
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="USER_PASSWORD_RESET_REQUESTED",
                entity_type="User",
                entity_id=user.id,
            )
            await db.commit()
            reset_url = f"{settings.PUBLIC_APP_URL.rstrip('/')}/reset-password?token={raw_token}"
            background_tasks.add_task(send_password_reset_email, user.email, reset_url)

    return FORGOT_PASSWORD_RESPONSE


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordIn, db: AsyncSession = Depends(get_db)):
    """Redeem a "forgot password" token (from the emailed link) and set a
    new password. Invalidates every other active session on the account —
    a password reset should log out anyone else still signed in."""
    user = await consume_reset_token(db, payload.token)

    if len(payload.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    user.password_hash = await hash_password_async(payload.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    await revoke_all_refresh_tokens(db, user.id)

    await log_action(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="USER_PASSWORD_RESET",
        entity_type="User",
        entity_id=user.id,
    )
    await db.commit()
    return {"message": "Password updated. You can now log in with your new password."}
