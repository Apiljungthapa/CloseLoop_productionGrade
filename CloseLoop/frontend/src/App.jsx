import React from 'react';
import { Routes, Route, Navigate, Outlet, useParams } from 'react-router-dom';
import { useAuth } from './auth.jsx';
import Layout from './components/Layout.jsx';
import Login from './pages/Login.jsx';
import ForgotPassword from './pages/ForgotPassword.jsx';
import ResetPassword from './pages/ResetPassword.jsx';
import AdminDashboardPage from './pages/AdminDashboardPage.jsx';
import AdminPackDetailPage from './pages/AdminPackDetailPage.jsx';
import SuperAdminReports from './pages/SuperAdminReports.jsx';
import LeadDashboard from './pages/LeadDashboard.jsx';
import LeadPackWorkspace from './pages/LeadPackWorkspace.jsx';
import TeamLeadReports from './pages/TeamLeadReports.jsx';
import ReportStudio from './pages/ReportStudio.jsx';
import MemberDashboard from './pages/MemberDashboard.jsx';
import MemberPackView from './pages/MemberPackView.jsx';
import MemberTasksView from './pages/MemberTasksView.jsx';
import TeamMembersPage from './pages/TeamMembersPage.jsx';
import DeadlinesPage from './pages/DeadlinesPage.jsx';
import TimeLogPage from './pages/TimeLogPage.jsx';
import HrmsIntegrationPage from './pages/HrmsIntegrationPage.jsx';
import PlanDetail from './pages/PlanDetail.jsx';
import OrganizationPage from './pages/PeriodPlanner.jsx'; // file now exports OrganizationPage
import PeriodsPage from './pages/PeriodsPage.jsx';
import ClosePacksPage from './pages/ClosePacksPage.jsx';
import CarryForwardPage from './pages/CarryForwardPage.jsx';
import Users from './pages/Users.jsx';
import Tenants from './pages/Tenants.jsx';
import HolidaysPage from './pages/HolidaysPage.jsx';
import Profile from './pages/Profile.jsx';
import { useHolidaysVersion } from './components/ui/workingDays';

// Routes that aren't scoped to any tenant — kept outside the /:orgSlug
// subtree below.
function Protected({ children }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

// Sends a logged-in user wherever they belong: a Platform Owner (no
// tenant) to /tenants, everyone else into their own /<tenant-slug>. Used
// for "/", an already-authenticated hit on "/login", and the catch-all.
function HomeRedirect() {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (user.role === 'PLATFORM_OWNER' || !user.tenant_slug) {
    return <Navigate to="/tenants" replace />;
  }
  return <Navigate to={`/${user.tenant_slug}`} replace />;
}

// Guards the /:orgSlug subtree: the URL's slug must match the signed-in
// user's own tenant. The API already refuses cross-tenant data regardless
// (tenant scoping there comes from the JWT, never the URL) — this is the
// UI-side check, so mistyping/guessing another org's slug in the address
// bar bounces you back to your own org instead of rendering anything.
function OrgGuard({ children }) {
  const { user } = useAuth();
  const { orgSlug } = useParams();
  if (!user) return <Navigate to="/login" replace />;
  if (user.role === 'PLATFORM_OWNER' || !user.tenant_slug) {
    return <Navigate to="/tenants" replace />;
  }
  if (orgSlug !== user.tenant_slug) {
    return <Navigate to={`/${user.tenant_slug}`} replace />;
  }
  return <Layout>{children}</Layout>;
}

function RoleRouter() {
  const { user } = useAuth();
  if (user.role === 'SUPER_ADMIN') return <AdminDashboardPage />;
  if (user.role === 'TEAM_LEAD') return <LeadDashboard />;
  return <MemberDashboard />;
}

// Role-gated wrapper: redirects to the org home if user lacks any of the
// allowed roles. Only ever rendered inside OrgGuard, so `user.tenant_slug`
// is always set here.
function RequireRole({ roles, children }) {
  const { user } = useAuth();
  if (!roles.includes(user.role)) return <Navigate to={`/${user.tenant_slug}`} replace />;
  return children;
}

export default function App() {
  const { user } = useAuth();
  // The holidays load a moment after sign-in; subscribing here re-renders the
  // pages below once they arrive, so due labels and pickers pick them up.
  useHolidaysVersion();
  return (
    <Routes>
      <Route path="/login" element={user ? <HomeRedirect /> : <Login />} />
      <Route path="/forgot-password" element={user ? <HomeRedirect /> : <ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/" element={<HomeRedirect />} />

      {/* My Profile (all roles, self-service) — identity, not tenant data,
          so it stays outside the /<orgSlug> prefix. */}
      <Route path="/profile" element={<Protected><Profile /></Protected>} />

      {/* Tenants (PLATFORM_OWNER only) — the owner isn't scoped to a tenant */}
      <Route
        path="/tenants"
        element={
          <Protected>
            <RequireRoleGlobal roles={['PLATFORM_OWNER']}>
              <Tenants />
            </RequireRoleGlobal>
          </Protected>
        }
      />

      {/* Holidays (PLATFORM_OWNER only) — one list shared by every tenant */}
      <Route
        path="/holidays"
        element={
          <Protected>
            <RequireRoleGlobal roles={['PLATFORM_OWNER']}>
              <HolidaysPage />
            </RequireRoleGlobal>
          </Protected>
        }
      />

      {/* Every tenant-scoped page lives under /<orgSlug>/... */}
      <Route path="/:orgSlug" element={<OrgGuard><Outlet /></OrgGuard>}>
        <Route index element={<RoleRouter />} />

        {/* Plan detail (all roles within tenant) */}
        <Route path="plans/:id" element={<PlanDetail />} />

        {/* Team Lead pack workspace */}
        <Route
          path="packs/:id"
          element={
            <RequireRole roles={['TEAM_LEAD']}>
              <LeadPackWorkspace />
            </RequireRole>
          }
        />

        {/* Team Member pack view — their own tasks in one pack */}
        <Route
          path="my-packs/:id"
          element={
            <RequireRole roles={['TEAM_MEMBER']}>
              <MemberPackView />
            </RequireRole>
          }
        />

        {/* All team members + their workload (Team Lead / Admin) — the
            "View All Members" link on the dashboard's Team Workload panel */}
        <Route
          path="team/members"
          element={
            <RequireRole roles={['TEAM_LEAD', 'SUPER_ADMIN']}>
              <TeamMembersPage />
            </RequireRole>
          }
        />

        {/* One team member's assigned tasks (Team Lead / Admin) */}
        <Route
          path="team/members/:userId"
          element={
            <RequireRole roles={['TEAM_LEAD', 'SUPER_ADMIN']}>
              <MemberTasksView />
            </RequireRole>
          }
        />

        {/* Super Admin pack detail — tenant-wide */}
        <Route
          path="admin-packs/:id"
          element={
            <RequireRole roles={['SUPER_ADMIN']}>
              <AdminPackDetailPage />
            </RequireRole>
          }
        />

        {/* Organization / entity hierarchy management (SA only) */}
        <Route
          path="organization"
          element={
            <RequireRole roles={['SUPER_ADMIN']}>
              <OrganizationPage />
            </RequireRole>
          }
        />
        {/* legacy URL — keep working for back-compat */}
        <Route
          path="period-planner"
          element={
            <RequireRole roles={['SUPER_ADMIN']}>
              <OrganizationPage />
            </RequireRole>
          }
        />

        {/* Periods (SA only) */}
        <Route
          path="periods"
          element={
            <RequireRole roles={['SUPER_ADMIN']}>
              <PeriodsPage />
            </RequireRole>
          }
        />

        {/* Close Packs (SA only) */}
        <Route
          path="close-packs"
          element={
            <RequireRole roles={['SUPER_ADMIN']}>
              <ClosePacksPage />
            </RequireRole>
          }
        />

        {/* Carry Forward queue (SA / TEAM_LEAD) */}
        <Route
          path="carry-forwards"
          element={
            <RequireRole roles={['SUPER_ADMIN', 'TEAM_LEAD']}>
              <CarryForwardPage />
            </RequireRole>
          }
        />

        {/* Full upcoming-deadlines list (Team Lead + Team Member) */}
        <Route
          path="deadlines"
          element={
            <RequireRole roles={['TEAM_LEAD', 'TEAM_MEMBER']}>
              <DeadlinesPage />
            </RequireRole>
          }
        />

        {/* HRMS Integration form (Team Lead + Team Member) */}
        <Route
          path="hrms-integration"
          element={
            <RequireRole roles={['TEAM_LEAD', 'TEAM_MEMBER']}>
              <HrmsIntegrationPage />
            </RequireRole>
          }
        />

        {/* My time-log history (Team Lead + Team Member) */}
        <Route
          path="time-log"
          element={
            <RequireRole roles={['TEAM_LEAD', 'TEAM_MEMBER']}>
              <TimeLogPage />
            </RequireRole>
          }
        />

        {/* A team member's time-log history (Team Lead only) — the same page
            as /time-log, opened from the Users list */}
        <Route
          path="users/:userId/time-log"
          element={
            <RequireRole roles={['TEAM_LEAD']}>
              <TimeLogPage />
            </RequireRole>
          }
        />

        {/* Users (SA + everyone for self-edit) */}
        <Route path="users" element={<Users />} />

        {/* Reports */}
        <Route
          path="reports/studio"
          element={
            <RequireRole roles={['SUPER_ADMIN', 'TEAM_LEAD']}>
              <ReportStudio />
            </RequireRole>
          }
        />
        <Route
          path="reports/superadmin"
          element={
            <RequireRole roles={['SUPER_ADMIN']}>
              <SuperAdminReports />
            </RequireRole>
          }
        />
        <Route
          path="reports/teamlead"
          element={
            <RequireRole roles={['SUPER_ADMIN', 'TEAM_LEAD']}>
              <TeamLeadReports />
            </RequireRole>
          }
        />

        {/* Unknown path under a valid org — back to that org's home */}
        <Route path="*" element={<Navigate to="." replace />} />
      </Route>

      <Route path="*" element={<HomeRedirect />} />
    </Routes>
  );
}

// Role gate for the handful of routes outside /:orgSlug (/tenants and
// /holidays today) — same idea as RequireRole but without assuming tenant_slug exists.
function RequireRoleGlobal({ roles, children }) {
  const { user } = useAuth();
  if (!roles.includes(user.role)) return <Navigate to="/" replace />;
  return children;
}
