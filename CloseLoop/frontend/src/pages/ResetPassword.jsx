import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import { authPageStyles } from './authPageStyles.js';

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const navigate = useNavigate();

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState('');

  async function submit(e) {
    e.preventDefault();
    setErr('');
    if (password.length < 8) {
      setErr('Password must be at least 8 characters.');
      return;
    }
    if (password !== confirm) {
      setErr('Passwords do not match.');
      return;
    }
    setLoading(true);
    try {
      await api.post('/auth/reset-password', { token, new_password: password });
      setDone(true);
      setTimeout(() => navigate('/login'), 1500);
    } catch (e) {
      setErr(getErrorMessage(e, 'This reset link is invalid or has expired.'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <style>{authPageStyles}</style>
      <div className="login-bg">
        <div className="login-orb login-orb-1" />
        <div className="login-orb login-orb-2" />
        <div className="login-orb login-orb-3" />

        <div className="login-card">
          <div className="cl-logo-wrap">
            <div className="cl-logo-icon">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none"
                stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/>
                <path d="M12 8v4l3 3"/>
                <path d="M16.5 3.5A9 9 0 0 1 21 12"/>
              </svg>
            </div>
            <div className="cl-brand-name">CloseLoop</div>
            <div className="cl-brand-sub">Choose a new password</div>
          </div>

          {!token ? (
            <>
              <div className="cl-err">This link is missing its reset token.</div>
              <div className="cl-footer">
                <Link to="/forgot-password">Request a new link</Link>
              </div>
            </>
          ) : done ? (
            <div className="cl-info-box">
              Password updated. Taking you to sign in…
            </div>
          ) : (
            <>
              <form onSubmit={submit}>
                <div className="cl-field-group" style={{ animationDelay: '0.18s' }}>
                  <label className="cl-label" htmlFor="password">New password</label>
                  <div className="cl-field-wrap">
                    <span className="cl-field-icon">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="3" y="11" width="18" height="11" rx="2"/>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                      </svg>
                    </span>
                    <input
                      id="password"
                      className="cl-input"
                      type={showPass ? 'text' : 'password'}
                      placeholder="At least 8 characters"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                    />
                    <button
                      type="button"
                      className="cl-eye-btn"
                      onClick={() => setShowPass((s) => !s)}
                      aria-label={showPass ? 'Hide password' : 'Show password'}
                    >
                      {showPass ? (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
                          <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
                          <line x1="1" y1="1" x2="23" y2="23"/>
                        </svg>
                      ) : (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                          <circle cx="12" cy="12" r="3"/>
                        </svg>
                      )}
                    </button>
                  </div>
                </div>

                <div className="cl-field-group" style={{ animationDelay: '0.24s' }}>
                  <label className="cl-label" htmlFor="confirm">Confirm password</label>
                  <div className="cl-field-wrap">
                    <span className="cl-field-icon">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="3" y="11" width="18" height="11" rx="2"/>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                      </svg>
                    </span>
                    <input
                      id="confirm"
                      className="cl-input"
                      type={showPass ? 'text' : 'password'}
                      placeholder="Re-enter password"
                      value={confirm}
                      onChange={(e) => setConfirm(e.target.value)}
                      required
                    />
                  </div>
                </div>

                {err && <div className="cl-err">{err}</div>}

                <button type="submit" className="cl-submit-btn" disabled={loading}>
                  {loading ? <><span className="cl-spinner" />Updating…</> : 'Update password'}
                </button>
              </form>

              <div className="cl-divider">
                <div className="cl-divider-line" />
                <span className="cl-divider-text">or</span>
                <div className="cl-divider-line" />
              </div>

              <div className="cl-footer">
                <Link to="/login">Back to sign in</Link>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
