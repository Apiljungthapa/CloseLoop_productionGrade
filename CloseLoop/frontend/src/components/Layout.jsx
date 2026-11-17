import React from 'react';
import { Container } from 'react-bootstrap';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../auth.jsx';
import { buildOrgPath } from '../orgPath.js';
import AppTopBar from './layout/AppTopBar.jsx';
import ChatWidget from './chat/ChatWidget.jsx';
import TimesheetBanner from './TimesheetBanner.jsx';

// Flat nav for the samrat-style top bar — one per role. `to` and `match`
// are written relative to the tenant root ('/' = the org's own dashboard);
// buildNav() below prefixes `to` with /<orgSlug> and match() is checked
// against the pathname with that same prefix stripped off, so these read
// exactly as they did before tenant slugs existed in the URL.
const TL_NAV = [
  { label: 'Dashboard', to: '/', match: (p) => p === '/' || p.startsWith('/packs') },
  { label: 'Carry Forward', to: '/carry-forwards', match: (p) => p.startsWith('/carry-forwards') },
  { label: 'HRMS Integration', to: '/hrms-integration', match: (p) => p.startsWith('/hrms-integration') },
  { label: 'Users', to: '/users', match: (p) => p.startsWith('/users') },
  { label: 'Reports', to: '/reports/teamlead', match: (p) => p.startsWith('/reports') },
];
const SA_NAV = [
  { label: 'Dashboard', to: '/', match: (p) => p === '/' },
  { label: 'Organization', to: '/organization', match: (p) => p === '/organization' || p === '/period-planner' },
  { label: 'Periods', to: '/periods', match: (p) => p === '/periods' },
  { label: 'Close Packs', to: '/close-packs', match: (p) => p.startsWith('/close-packs') },
  { label: 'Carry Forward', to: '/carry-forwards', match: (p) => p.startsWith('/carry-forwards') },
  { label: 'Users', to: '/users', match: (p) => p.startsWith('/users') },
  { label: 'Reports', to: '/reports/superadmin', match: (p) => p.startsWith('/reports') },
];
const MEMBER_NAV = [
  { label: 'Overview', to: '/', match: (p) => p === '/' || p.startsWith('/my-packs') },
  { label: 'HRMS Integration', to: '/hrms-integration', match: (p) => p.startsWith('/hrms-integration') },
];
// The Platform Owner has no tenant — /tenants stays a top-level route, not
// prefixed with any org slug.
const OWNER_NAV = [
  { label: 'Tenants', to: '/tenants', match: (p) => p.startsWith('/tenants') },
  { label: 'Holidays', to: '/holidays', match: (p) => p.startsWith('/holidays') },
];

function buildNav(items, orgSlug) {
  const prefix = orgSlug ? `/${orgSlug}` : '';
  return items.map((item) => ({
    ...item,
    to: buildOrgPath(orgSlug, item.to),
    // AppTopBar calls match() with the full pathname (/<orgSlug>/...) —
    // strip the prefix back off so these read the same relative patterns
    // ('/', '/organization', ...) as before tenant slugs existed in the URL.
    match: (pathname) => {
      const rel = prefix && pathname.startsWith(prefix) ? pathname.slice(prefix.length) || '/' : pathname;
      return item.match(rel);
    },
  }));
}

export default function Layout({ children }) {
  const { user } = useAuth();
  const loc = useLocation();
  const isOwner = user.role === 'PLATFORM_OWNER';
  const isSA = user.role === 'SUPER_ADMIN';
  const isTL = user.role === 'TEAM_LEAD';
  const isMember = user.role === 'TEAM_MEMBER';
  const orgSlug = user.tenant_slug;

  const navItems = isSA ? SA_NAV : isMember ? MEMBER_NAV : isOwner ? OWNER_NAV : TL_NAV;
  const nav = isOwner ? navItems : buildNav(navItems, orgSlug);

  // Pathname relative to the org prefix, so the match()/fullBleed logic
  // below is unchanged from before tenant slugs existed in the URL.
  const prefix = orgSlug ? `/${orgSlug}` : '';
  const p = !isOwner && prefix && loc.pathname.startsWith(prefix)
    ? loc.pathname.slice(prefix.length) || '/'
    : loc.pathname;

  // The dashboards / pack views render full-bleed (their own .page-wrap
  // handles width/padding); every other page keeps the standard container.
  const fullBleed =
    p === '/deadlines' ||
    p === '/time-log' ||
    /^\/users\/[^/]+\/time-log$/.test(p) ||
    p === '/hrms-integration' ||
    p === '/users' ||
    (isTL && (p === '/' || p.startsWith('/packs/'))) ||
    (isMember && (p === '/' || p.startsWith('/my-packs/'))) ||
    (isSA && (p === '/' || p.startsWith('/admin-packs/') || p.startsWith('/close-packs')));

  return (
    <>
      <AppTopBar nav={nav} />
      {!isOwner && <TimesheetBanner />}
      {fullBleed ? (
        children
      ) : (
        <Container fluid className="py-4" style={{ maxWidth: 1400 }}>
          {children}
        </Container>
      )}
      {/* Close Bhai — renders nothing unless the server has the assistant on for this user. */}
      <ChatWidget />
    </>
  );
}
