"""Platform Owner only — manage tenants."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Tenant, User, Role
from ..schemas import TenantIn, TenantOut, TenantCreatedOut, TenantCredentialsOut
from ..auth import require_roles, hash_password_async
from ..services.audit_service import log_action
from ..services.tenant_credentials_service import issue_credentials, rotate_secret
from ..services.tenant_slug_service import slugify, unique_slug, validate_slug_format

router = APIRouter(
    prefix="/api/tenants",
    tags=["tenants"],
    dependencies=[Depends(require_roles(Role.PLATFORM_OWNER))],
)


@router.get("", response_model=list[TenantOut])
async def list_tenants(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return [TenantOut.model_validate(t) for t in rows.scalars().all()]


@router.post("", response_model=TenantCreatedOut, status_code=201)
async def create_tenant(
    payload: TenantIn,
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(Tenant).where(Tenant.name == payload.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Tenant '{payload.name}' already exists.")

    if payload.slug:
        candidate = slugify(payload.slug)
        err = validate_slug_format(candidate)
        if err:
            raise HTTPException(422, err)
        slug = await unique_slug(db, candidate)
        if slug != candidate:
            raise HTTPException(409, f"Slug '{candidate}' is already taken.")
    else:
        # Auto-generate from the name — silently de-duplicated (acme,
        # acme-2, ...) since this path never conflicts on its own.
        slug = await unique_slug(db, slugify(payload.name))

    t = Tenant(
        name=payload.name, slug=slug, status=payload.status, plan_limit=payload.plan_limit
    )
    db.add(t)
    await db.flush()

    # Auto-provision this tenant's OAuth2 client-credentials pair for
    # machine-to-machine API access — every tenant gets one automatically,
    # no manual setup step. The raw secret is only ever visible in this
    # response.
    raw_client_secret = issue_credentials(t)

    # Optionally create the first SUPER_ADMIN inside the new tenant
    if payload.super_admin:
        sa = payload.super_admin
        existing_user = (
            await db.execute(select(User).where(User.email == sa.email))
        ).scalar_one_or_none()
        if existing_user:
            raise HTTPException(409, f"User with email '{sa.email}' already exists.")
        user = User(
            tenant_id=t.id,
            email=sa.email,
            name=sa.name,
            role=Role.SUPER_ADMIN,
            password_hash=await hash_password_async(sa.password),
        )
        db.add(user)
        await db.flush()
        await log_action(
            db,
            tenant_id=t.id,
            user_id=None,
            action="TENANT_SUPER_ADMIN_CREATED",
            entity_type="User",
            entity_id=user.id,
            meta={"email": sa.email},
        )

    await log_action(
        db,
        tenant_id=t.id,
        user_id=None,
        action="TENANT_CREATED",
        entity_type="Tenant",
        entity_id=t.id,
        meta={"name": t.name},
    )
    await db.commit()
    await db.refresh(t)
    return TenantCreatedOut(
        **TenantOut.model_validate(t).model_dump(), client_secret=raw_client_secret
    )


@router.post("/{tenant_id}/rotate-credentials", response_model=TenantCredentialsOut)
async def rotate_tenant_credentials(tenant_id: str, db: AsyncSession = Depends(get_db)):
    """Regenerate a tenant's client_secret (client_id stays stable). The
    old secret stops working immediately — any integration using it needs
    reconfiguring with the new value returned here, which is shown once."""
    t = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Not found")
    if not t.client_id:
        # Pre-existing tenants created before this feature shipped won't
        # have a client_id yet — issue a full pair instead of rotating.
        raw = issue_credentials(t)
    else:
        raw = rotate_secret(t)
    await log_action(
        db,
        tenant_id=t.id,
        user_id=None,
        action="TENANT_API_CREDENTIALS_ROTATED",
        entity_type="Tenant",
        entity_id=t.id,
    )
    await db.commit()
    await db.refresh(t)
    return TenantCredentialsOut(tenant_id=t.id, client_id=t.client_id, client_secret=raw)


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: str, payload: TenantIn, db: AsyncSession = Depends(get_db)
):
    t = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Not found")
    t.name = payload.name
    t.status = payload.status
    t.plan_limit = payload.plan_limit

    if payload.slug:
        candidate = slugify(payload.slug)
        err = validate_slug_format(candidate)
        if err:
            raise HTTPException(422, err)
        if candidate != t.slug:
            deduped = await unique_slug(db, candidate, exclude_tenant_id=t.id)
            if deduped != candidate:
                raise HTTPException(409, f"Slug '{candidate}' is already taken.")
            t.slug = candidate
            # NOTE: changing a live tenant's slug breaks every bookmarked/
            # shared /<old-slug>/... link for its users — surface a warning
            # in the tenant-edit UI before calling this with a new slug.

    await db.commit()
    await db.refresh(t)
    return TenantOut.model_validate(t)


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(tenant_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Not found")
    await db.delete(t)
    await db.commit()
