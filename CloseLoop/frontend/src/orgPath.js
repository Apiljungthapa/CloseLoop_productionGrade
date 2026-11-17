// Builds tenant-scoped URLs — every in-app route lives under
// /<tenant-slug>/... (dashboard, packs/:id, plans/:id, ...) except the
// handful of routes that aren't scoped to a tenant at all (/login,
// /tenants for the Platform Owner, /profile).
//
// The prefix comes from the authenticated user's own session
// (`user.tenant_slug`, set by the backend at login/`/auth/me`) rather than
// the current URL — so it's correct even on a page like /profile that
// isn't itself under the org prefix, and a Platform Owner (no tenant)
// simply gets paths back unprefixed.
import { useAuth } from './auth.jsx';

export function buildOrgPath(slug, path) {
  if (!slug) return path; // no tenant (Platform Owner) — leave as-is
  const suffix = path === '/' || path === '' ? '' : path.startsWith('/') ? path : `/${path}`;
  return `/${slug}${suffix}`;
}

export function useOrgPath() {
  const { user } = useAuth();
  const slug = user?.tenant_slug;
  return (path) => buildOrgPath(slug, path);
}
