// Shared visual language for the unauthenticated auth pages (Login,
// ForgotPassword, ResetPassword) — extracted from Login.jsx so the three
// pages read as one flow instead of three copies of the same CSS.
export const authPageStyles = `
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Serif+Display&display=swap');

  .login-bg {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #0a0f1e;
    padding: 2rem;
    position: relative;
    overflow: hidden;
    font-family: 'DM Sans', sans-serif;
  }

  .login-orb {
    position: absolute;
    border-radius: 50%;
    filter: blur(60px);
    opacity: 0.35;
    animation: cl-drift 8s ease-in-out infinite alternate;
    pointer-events: none;
  }
  .login-orb-1 { width: 380px; height: 380px; background: #2563eb; top: -100px; left: -80px; animation-delay: 0s; }
  .login-orb-2 { width: 300px; height: 300px; background: #7c3aed; bottom: -80px; right: -60px; animation-delay: -3s; }
  .login-orb-3 { width: 200px; height: 200px; background: #0ea5e9; bottom: 25%; left: 35%; animation-delay: -5s; }

  @keyframes cl-drift {
    0%   { transform: translate(0, 0) scale(1); }
    100% { transform: translate(20px, -20px) scale(1.08); }
  }

  .login-card {
    position: relative;
    width: 100%;
    max-width: 400px;
    background: rgba(255, 255, 255, 0.04);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 20px;
    padding: 2.5rem 2rem;
    animation: cl-slideUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) both;
  }

  @keyframes cl-slideUp {
    from { opacity: 0; transform: translateY(32px); }
    to   { opacity: 1; transform: translateY(0); }
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
    width: 54px;
    height: 54px;
    border-radius: 14px;
    background: linear-gradient(135deg, #2563eb, #7c3aed);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 0.85rem;
    animation: cl-pulse-glow 3s ease-in-out infinite;
  }

  @keyframes cl-pulse-glow {
    0%, 100% { box-shadow: 0 8px 32px rgba(37, 99, 235, 0.4); }
    50%       { box-shadow: 0 8px 48px rgba(124, 58, 237, 0.6); }
  }

  .cl-brand-name {
    font-family: 'DM Serif Display', serif;
    font-size: 1.65rem;
    color: #fff;
    letter-spacing: -0.02em;
    line-height: 1;
  }

  .cl-brand-sub {
    font-size: 0.78rem;
    color: rgba(255, 255, 255, 0.4);
    margin-top: 5px;
    letter-spacing: 0.02em;
  }

  .cl-field-group {
    margin-bottom: 1.1rem;
    animation: cl-slideUp 0.6s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }

  .cl-label {
    display: block;
    font-size: 0.72rem;
    font-weight: 500;
    color: rgba(255, 255, 255, 0.5);
    margin-bottom: 6px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  .cl-field-wrap {
    position: relative;
  }

  .cl-field-icon {
    position: absolute;
    left: 12px;
    top: 50%;
    transform: translateY(-50%);
    color: rgba(255, 255, 255, 0.28);
    pointer-events: none;
    font-size: 16px;
    line-height: 1;
  }

  .cl-input {
    width: 100%;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
    color: #fff;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem;
    padding: 10px 38px 10px 38px;
    outline: none;
    transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
  }

  .cl-input::placeholder { color: rgba(255, 255, 255, 0.22); }

  .cl-input:focus {
    border-color: rgba(99, 130, 241, 0.7);
    background: rgba(255, 255, 255, 0.09);
    box-shadow: 0 0 0 3px rgba(99, 130, 241, 0.15);
  }

  .cl-eye-btn {
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    cursor: pointer;
    color: rgba(255, 255, 255, 0.32);
    font-size: 16px;
    padding: 3px;
    line-height: 1;
    transition: color 0.2s;
  }
  .cl-eye-btn:hover { color: rgba(255, 255, 255, 0.65); }

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
    background: rgba(37, 99, 235, 0.12);
    border: 1px solid rgba(37, 99, 235, 0.28);
    border-radius: 8px;
    color: #bfdbfe;
    font-size: 0.83rem;
    line-height: 1.5;
    padding: 12px 14px;
    margin-bottom: 1.2rem;
    animation: cl-slideUp 0.4s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  .cl-info-box b { color: #fff; }

  .cl-submit-btn {
    width: 100%;
    padding: 11px;
    border-radius: 10px;
    border: none;
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem;
    font-weight: 500;
    letter-spacing: 0.01em;
    color: #fff;
    background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%);
    position: relative;
    overflow: hidden;
    transition: transform 0.15s, box-shadow 0.15s, background 0.4s;
    animation: cl-slideUp 0.6s 0.32s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }

  .cl-submit-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: 0 8px 28px rgba(37, 99, 235, 0.45);
  }
  .cl-submit-btn:active:not(:disabled) { transform: translateY(0); }
  .cl-submit-btn:disabled { opacity: 0.65; cursor: not-allowed; }
  .cl-submit-btn.success { background: linear-gradient(135deg, #059669, #10b981); }

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

  .cl-check {
    display: inline-block;
    animation: cl-pop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    margin-right: 4px;
  }
  @keyframes cl-pop {
    from { transform: scale(0) rotate(-45deg); opacity: 0; }
    to   { transform: scale(1) rotate(0deg); opacity: 1; }
  }

  .cl-divider {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 1.2rem 0;
    animation: cl-slideUp 0.6s 0.38s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }
  .cl-divider-line { flex: 1; height: 1px; background: rgba(255, 255, 255, 0.08); }
  .cl-divider-text { font-size: 0.72rem; color: rgba(255, 255, 255, 0.28); }

  .cl-footer {
    text-align: center;
    font-size: 0.75rem;
    color: rgba(255, 255, 255, 0.3);
    animation: cl-slideUp 0.6s 0.44s cubic-bezier(0.22, 1, 0.36, 1) both;
    opacity: 0;
    animation-fill-mode: forwards;
  }
  .cl-footer a { color: rgba(130, 150, 241, 0.9); text-decoration: none; }
  .cl-footer a:hover { color: #a5b4fc; }
`;
