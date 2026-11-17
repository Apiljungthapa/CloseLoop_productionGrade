"""Tenant URL slugs — the `<slug>` segment of every in-app route
(https://.../<slug>/dashboard, .../<slug>/packs/123, …).

Kept separate from `Tenant.name` (a free-text display label) so renaming a
tenant never breaks a bookmarked/shared link — the slug only ever changes
when a Platform Owner explicitly edits it.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Tenant

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Anything that collides with a top-level frontend route (login/signup,
# platform-owner pages, or a static asset path) or an API prefix. A tenant
# named one of these would be unreachable at /<slug>/... — reject it up
# front rather than produce a silently-broken tenant.
RESERVED_SLUGS = {
    "login", "logout", "signup", "register",
    "tenants", "profile", "users",
    "api", "ws", "assets", "static", "favicon.ico",
    "admin", "owner", "app", "www",
}

MIN_LENGTH = 2
MAX_LENGTH = 63  # generous headroom under the column's 80-char limit


def slugify(text: str) -> str:
    """Lowercase, ASCII, hyphen-separated. 'Acme Finance Inc.' -> 'acme-finance-inc'."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug[:MAX_LENGTH].strip("-") or "org"


def validate_slug_format(slug: str) -> str | None:
    """Return an error message if `slug` isn't a well-formed candidate
    (independent of whether it's already taken) — None if it's fine."""
    if not (MIN_LENGTH <= len(slug) <= MAX_LENGTH):
        return f"Slug must be {MIN_LENGTH}-{MAX_LENGTH} characters."
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        return "Slug may only contain lowercase letters, numbers, and hyphens (not at the ends)."
    if slug in RESERVED_SLUGS:
        return f"'{slug}' is reserved and can't be used as a tenant slug."
    return None


async def unique_slug(db: AsyncSession, base: str, *, exclude_tenant_id: str | None = None) -> str:
    """`base` (already slugified) with a `-2`, `-3`, … suffix appended until
    it doesn't collide with an existing tenant. Used both for auto-generating
    a slug from the tenant name and for resolving a collision on an
    explicitly-requested one."""
    candidate = base
    suffix = 2
    while True:
        q = select(Tenant.id).where(Tenant.slug == candidate)
        if exclude_tenant_id:
            q = q.where(Tenant.id != exclude_tenant_id)
        exists = (await db.execute(q)).scalar_one_or_none()
        if not exists:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1
