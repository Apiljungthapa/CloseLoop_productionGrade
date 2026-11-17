// Forgot Password — same shell/style/animations as Login.jsx (kept
// self-contained here, same as Login, rather than sharing a module) so the
// two pages read as one continuous flow. ResetPassword.jsx is untouched —
// it still uses the older authPageStyles.js.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import api, { getErrorMessage } from '../api';

const styles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=Poppins:wght@600;700;800&display=swap');

  .login-page {
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: stretch;
    background: #050a16 url('/login-bg.png') center / cover no-repeat;
    font-family: 'DM Sans', sans-serif;
    overflow-y: auto;
  }

  .login-shell {
    display: flex;
    align-items: stretch;
    width: 100%;
    max-width: 1440px;
    margin: 0 auto;
  }

  .login-left {
    flex: 1 1 55%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
    gap: 1.25rem;
    padding: 2.25rem clamp(2rem, 6vw, 4.5rem) 2.25rem clamp(2.5rem, 8vw, 6rem);
    max-width: 800px;
  }

  .login-tagline-block {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
  }

  .cl-wordmark-lockup {
    width: clamp(260px, 23vw, 380px);
    height: auto;
    display: block;
  }
  .w-close { color: #fff; }
  .w-loop { color: #4f9dff; }

  .login-tagline {
    margin: 0;
    font-family: 'Poppins', 'DM Sans', sans-serif;
    font-size: clamp(1.9rem, 3.4vw, 2.6rem);
    font-weight: 700;
    line-height: 1.25;
    color: #fff;
    letter-spacing: -0.01em;
  }
  .login-tagline .accent {
    background: linear-gradient(90deg, #60a5fa, #3b82f6);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  .login-sub {
    margin: 0;
    max-width: 460px;
    font-size: 1rem;
    line-height: 1.6;
    color: rgba(255, 255, 255, 0.55);
  }

  .login-features {
    display: flex;
    gap: 1.25rem;
    width: 100%;
  }

  .feature-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    flex: 1 1 0;
    min-width: 0;
  }

  .feature-icon {
    flex: none;
    width: 22px;
    height: 22px;
    color: #4f9dff;
    margin-top: 2px;
  }

  .feature-title {
    display: block;
    font-size: 0.9rem;
    font-weight: 600;
    color: #fff;
  }

  .feature-desc {
    display: block;
    font-size: 0.78rem;
    line-height: 1.4;
    color: rgba(255, 255, 255, 0.45);
    margin-top: 2px;
  }

  .login-illustration {
    position: relative;
    width: 100%;
    max-width: 420px;
    aspect-ratio: 460 / 300;
    margin-top: 0.25rem;
  }

  .board-panel {
    position: absolute;
    left: 50%;
    top: 50%;
    z-index: 2;
    transform: translate(-50%, -50%) rotate(-3deg) skewY(-2.5deg);
    display: flex;
    gap: 12px;
    width: 320px;
    background: linear-gradient(160deg, rgba(59, 130, 246, 0.16), rgba(10, 16, 32, 0.55));
    border: 1px solid rgba(96, 165, 250, 0.28);
    border-radius: 16px;
    padding: 14px;
    box-shadow: 0 25px 60px rgba(2, 8, 23, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    backdrop-filter: blur(3px);
  }

  .board-col {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }

  .board-col-head {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.64rem;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.8);
    margin-bottom: 2px;
    white-space: nowrap;
  }

  .ring-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    border: 1.5px solid rgba(255, 255, 255, 0.55);
    flex: none;
  }
  .ring-dot.blue { border-color: #60a5fa; }
  .ring-dot.green { border-color: #34d399; }

  .board-pill {
    height: 24px;
    border-radius: 7px;
    background: linear-gradient(135deg, rgba(147, 197, 253, 0.55), rgba(37, 99, 235, 0.28));
  }
  .board-pill.short { width: 70%; }
  .board-pill.done {
    background: linear-gradient(135deg, rgba(52, 211, 153, 0.5), rgba(16, 185, 129, 0.25));
    border: 1px solid rgba(52, 211, 153, 0.55);
    display: flex;
    align-items: center;
    gap: 6px;
    padding-left: 6px;
  }
  .pill-check {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #34d399;
    color: #04150f;
    font-size: 9px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    flex: none;
  }

  .login-right {
    flex: 1 1 45%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem;
  }

  @media (max-width: 960px) {
    .login-left { display: none; }
    .login-right { flex: 1 1 100%; }
  }

  .login-card {
    position: relative;
    width: 100%;
    max-width: 512px;
    background: rgba(255, 255, 255, 0.04);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(96, 165, 250, 0.35);
    border-radius: 22px;
    padding: 3.25rem 3rem;
    animation: cl-slideUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) both, cl-card-glow 4s ease-in-out 0.6s infinite;
  }

  @keyframes cl-slideUp {
    from { opacity: 0; transform: translateY(32px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  @keyframes cl-card-glow {
    0%, 100% {
      box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.12), 0 0 30px rgba(37, 99, 235, 0.28),
        0 0 70px rgba(59, 130, 246, 0.12), 0 25px 70px rgba(2, 8, 23, 0.65);
    }
    50% {
      box-shadow: 0 0 0 1px rgba(96, 165, 250, 0.22), 0 0 46px rgba(37, 99, 235, 0.42),
        0 0 100px rgba(59, 130, 246, 0.22), 0 25px 70px rgba(2, 8, 23, 0.65);
    }
  }

  .cl-logo-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin-bottom: 2rem;
    animation: cl-slideUp 0.6s 0.1s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }

  .cl-logo-icon {
    width: 88px;
    height: 88px;
    border-radius: 20px;
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(96, 165, 250, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 0.85rem;
    animation: cl-pulse-glow 3s ease-in-out infinite;
  }

  .cl-logo-icon.success {
    background: rgba(16, 185, 129, 0.12);
    border-color: rgba(52, 211, 153, 0.5);
    animation: cl-pulse-glow-green 3s ease-in-out infinite;
  }

  .cl-logo-icon-img {
    width: 50px;
    height: auto;
    display: block;
    filter: drop-shadow(0 0 8px rgba(96, 165, 250, 0.55));
  }

  @keyframes cl-pulse-glow {
    0%, 100% { box-shadow: 0 0 20px rgba(37, 99, 235, 0.35), inset 0 0 16px rgba(59, 130, 246, 0.1); }
    50%       { box-shadow: 0 0 34px rgba(59, 130, 246, 0.55), inset 0 0 22px rgba(59, 130, 246, 0.18); }
  }

  @keyframes cl-pulse-glow-green {
    0%, 100% { box-shadow: 0 0 20px rgba(16, 185, 129, 0.3), inset 0 0 16px rgba(52, 211, 153, 0.12); }
    50%       { box-shadow: 0 0 34px rgba(16, 185, 129, 0.5), inset 0 0 22px rgba(52, 211, 153, 0.2); }
  }

  .cl-brand-name {
    font-family: 'Poppins', 'DM Sans', sans-serif;
    font-size: 1.85rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    line-height: 1;
  }

  .cl-brand-sub {
    font-size: 0.88rem;
    color: rgba(255, 255, 255, 0.4);
    margin-top: 7px;
    letter-spacing: 0.02em;
    text-align: center;
  }

  .cl-field-group {
    margin-bottom: 1.6rem;
    animation: cl-slideUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }

  .cl-label {
    display: block;
    font-size: 0.78rem;
    font-weight: 500;
    color: rgba(255, 255, 255, 0.5);
    margin-bottom: 8px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  .cl-field-wrap {
    position: relative;
  }

  .cl-field-icon {
    position: absolute;
    left: 14px;
    top: 50%;
    transform: translateY(-50%);
    color: rgba(255, 255, 255, 0.28);
    pointer-events: none;
    font-size: 18px;
    line-height: 1;
  }

  .cl-input {
    width: 100%;
    background: rgba(15, 23, 42, 0.35);
    border: 1px solid rgba(147, 197, 253, 0.28);
    border-radius: 10px;
    color: #fff;
    font-family: 'DM Sans', sans-serif;
    font-size: 1.02rem;
    padding: 16px 44px 16px 44px;
    outline: none;
    transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
  }

  .cl-input::placeholder { color: rgba(255, 255, 255, 0.22); }

  .cl-input:focus {
    border-color: rgba(99, 130, 241, 0.7);
    background: rgba(255, 255, 255, 0.09);
    box-shadow: 0 0 0 3px rgba(99, 130, 241, 0.15);
  }

  .cl-err {
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.28);
    border-radius: 8px;
    color: #fca5a5;
    font-size: 0.8rem;
    padding: 8px 12px;
    margin-bottom: 1rem;
    animation: cl-shake 0.4s cubic-bezier(0.36, 0.07, 0.19, 0.97) both;
  }

  @keyframes cl-shake {
    10%, 90% { transform: translateX(-2px); }
    20%, 80% { transform: translateX(4px); }
    30%, 50%, 70% { transform: translateX(-4px); }
    40%, 60% { transform: translateX(4px); }
  }

  .cl-info-box {
    background: rgba(52, 211, 153, 0.08);
    border: 1px solid rgba(52, 211, 153, 0.3);
    border-radius: 12px;
    color: rgba(255, 255, 255, 0.75);
    font-size: 0.92rem;
    line-height: 1.55;
    padding: 16px 18px;
    margin-bottom: 1.6rem;
    text-align: center;
    animation: cl-slideUp 0.6s 0.18s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }
  .cl-info-box b { color: #fff; }

  .cl-submit-btn {
    width: 100%;
    padding: 17px;
    border-radius: 12px;
    border: none;
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-size: 1.02rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    background: linear-gradient(90deg, #2563eb 0%, #4f46e5 100%);
    position: relative;
    overflow: hidden;
    transition: transform 0.15s, box-shadow 0.15s, background 0.4s;
    animation: cl-slideUp 0.6s 0.32s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }

  .cl-btn-arrow { transition: transform 0.15s; }
  .cl-submit-btn:hover:not(:disabled) .cl-btn-arrow { transform: translateX(3px); }

  .cl-submit-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: 0 8px 28px rgba(37, 99, 235, 0.45);
  }
  .cl-submit-btn:active:not(:disabled) { transform: translateY(0); }
  .cl-submit-btn:disabled { opacity: 0.65; cursor: not-allowed; }

  .cl-spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid rgba(255, 255, 255, 0.35);
    border-top-color: #fff;
    border-radius: 50%;
    animation: cl-spin 0.7s linear infinite;
    vertical-align: -2px;
    margin-right: 6px;
  }
  @keyframes cl-spin { to { transform: rotate(360deg); } }

  .cl-divider {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 1.5rem 0;
    animation: cl-slideUp 0.6s 0.38s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }
  .cl-divider-line { flex: 1; height: 1px; background: rgba(255, 255, 255, 0.08); }
  .cl-divider-text {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 0.78rem;
    color: rgba(255, 255, 255, 0.35);
    white-space: nowrap;
  }

  .cl-footer {
    text-align: center;
    font-size: 0.82rem;
    color: rgba(255, 255, 255, 0.3);
    animation: cl-slideUp 0.6s 0.44s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }
  .cl-footer a { color: rgba(130, 150, 241, 0.9); text-decoration: none; }
  .cl-footer a:hover { color: #a5b4fc; }
`;

const FEATURES = [
  {
    title: 'Organize',
    desc: 'Keep your tasks in one place',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="3" />
        <path d="m8 12 3 3 5-6" />
      </svg>
    ),
  },
  {
    title: 'Collaborate',
    desc: 'Work better together',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
  },
  {
    title: 'Get it done',
    desc: 'Close every task, every time',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 1l4 4-4 4" />
        <path d="M3 11V9a4 4 0 0 1 4-4h14" />
        <path d="M7 23l-4-4 4-4" />
        <path d="M21 13v2a4 4 0 0 1-4 4H3" />
      </svg>
    ),
  },
];

function LoginIllustration() {
  return (
    <div className="login-illustration">
      <div className="board-panel">
        <div className="board-col">
          <div className="board-col-head"><span className="ring-dot" />To Do</div>
          <div className="board-pill" />
          <div className="board-pill short" />
        </div>
        <div className="board-col">
          <div className="board-col-head"><span className="ring-dot blue" />In Progress</div>
          <div className="board-pill" />
          <div className="board-pill short" />
        </div>
        <div className="board-col">
          <div className="board-col-head"><span className="ring-dot green" />Done</div>
          <div className="board-pill done"><span className="pill-check">✓</span></div>
          <div className="board-pill" />
        </div>
      </div>
    </div>
  );
}

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState('');
  const [errKey, setErrKey] = useState(0);

  async function submit(e) {
    e.preventDefault();
    setErr('');
    setLoading(true);
    try {
      // Backend always responds the same way whether or not the email is
      // registered — this UI can't and shouldn't tell the two apart either.
      await api.post('/auth/forgot-password', { email });
      setSent(true);
    } catch (e) {
      setErrKey((k) => k + 1);
      setErr(getErrorMessage(e, 'Something went wrong. Please try again.'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <style>{styles}</style>
      <div className="login-page">
       <div className="login-shell">
        <div className="login-left">
          <img src="/logo-full.svg" alt="CloseLoop" className="cl-wordmark-lockup" />

          <div className="login-tagline-block">
            <h1 className="login-tagline">
              Plan your work.<br />
              <span className="accent">Close the loop.</span>
            </h1>
            <p className="login-sub">
              A simple, powerful task management platform for teams that get things done.
            </p>
          </div>

          <div className="login-features">
            {FEATURES.map((f) => (
              <div className="feature-item" key={f.title}>
                <span className="feature-icon">{f.icon}</span>
                <span>
                  <span className="feature-title">{f.title}</span>
                  <span className="feature-desc">{f.desc}</span>
                </span>
              </div>
            ))}
          </div>

          <LoginIllustration />
        </div>

        <div className="login-right">
          <div className="login-card">
            {/* Logo */}
            <div className="cl-logo-wrap">
              <div className={`cl-logo-icon${sent ? ' success' : ''}`}>
                {sent ? (
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="none"
                    stroke="#34d399" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                ) : (
                  <img src="/logo-icon.svg" alt="CloseLoop" className="cl-logo-icon-img" />
                )}
              </div>
              <div className="cl-brand-name">
                <span className="w-close">Close</span><span className="w-loop">Loop</span>
              </div>
              <div className="cl-brand-sub">
                {sent ? 'Check your inbox' : 'Reset your password'}
              </div>
            </div>

            {sent ? (
              <>
                <div className="cl-info-box">
                  If an account exists for <b>{email}</b>, we&apos;ve sent a link to reset
                  the password. It expires in 30 minutes.
                </div>
                <div className="cl-footer">
                  <Link to="/login">Back to sign in</Link>
                </div>
              </>
            ) : (
              <>
                <form onSubmit={submit}>
                  {/* Email */}
                  <div className="cl-field-group" style={{ animationDelay: '0.18s' }}>
                    <label className="cl-label" htmlFor="email">Email</label>
                    <div className="cl-field-wrap">
                      <span className="cl-field-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="2" y="4" width="20" height="16" rx="2"/>
                          <path d="m2 7 10 7 10-7"/>
                        </svg>
                      </span>
                      <input
                        id="email"
                        className="cl-input"
                        type="email"
                        placeholder="you@company.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        required
                      />
                    </div>
                  </div>

                  {/* Error */}
                  {err && (
                    <div key={errKey} className="cl-err">
                      {err}
                    </div>
                  )}

                  {/* Submit */}
                  <button
                    type="submit"
                    className="cl-submit-btn"
                    disabled={loading}
                    style={{ animationDelay: '0.32s' }}
                  >
                    {loading ? (
                      <><span className="cl-spinner" />Sending…</>
                    ) : (
                      <>
                        Send reset link
                        <svg className="cl-btn-arrow" width="18" height="18" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M5 12h14" />
                          <path d="m13 6 6 6-6 6" />
                        </svg>
                      </>
                    )}
                  </button>
                </form>

                <div className="cl-divider">
                  <div className="cl-divider-line" />
                  <span className="cl-divider-text">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2 4 6v6c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V6l-8-4z" />
                    </svg>
                    Secure reset
                  </span>
                  <div className="cl-divider-line" />
                </div>

                <div className="cl-footer">
                  <Link to="/login">Back to sign in</Link>
                </div>
              </>
            )}
          </div>
        </div>
       </div>
      </div>
    </>
  );
}
