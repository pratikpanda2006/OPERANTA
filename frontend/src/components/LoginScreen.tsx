import React, { FormEvent, useState } from 'react';
import { login, register, getSessionToken } from '../api/client';
import { AuthUser } from '../api/types';
import { Button, Logo } from './ui';

type Mode = 'login' | 'register';

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function LoginScreen({ onSuccess }: { onSuccess: (user: AuthUser) => void }) {
  const [mode, setMode] = useState<Mode>('login');

  // Shared
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);

  // Registration-only
  const [businessName, setBusinessName] = useState('');
  const [fullName, setFullName] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [accessCode, setAccessCode] = useState('');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  const emailValid = EMAIL_RE.test(email.trim());
  const passwordValid = password.length >= 10;
  const confirmValid = confirmPassword.length > 0 && confirmPassword === password;
  const businessValid = businessName.trim().length > 0;
  const fullNameValid = fullName.trim().length > 0;

  const loginValid = emailValid && password.length > 0;
  const registerValid =
    businessValid && fullNameValid && emailValid && passwordValid && confirmValid;

  const mark = (field: string) => setTouched((t) => ({ ...t, [field]: true }));

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setTouched({});
    setPassword('');
    setConfirmPassword('');
    setAccessCode('');
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (mode === 'login') {
      if (!loginValid) {
        setTouched({ email: true, password: true });
        return;
      }
    } else {
      if (!registerValid) {
        setTouched({ business_name: true, full_name: true, email: true, password: true, confirm_password: true });
        return;
      }
    }

    setLoading(true);
    setError(null);
    try {
      const res =
        mode === 'login'
          ? await login(email, password)
          : await register({
              business_name: businessName.trim(),
              full_name: fullName.trim(),
              email,
              password,
              confirm_password: confirmPassword,
              workspace_access_code: accessCode.trim() || null,
            });
      onSuccess(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
    } finally {
      setLoading(false);
    }
  }

  // If a session token is already present, skip the form (parent decides the flow).
  React.useEffect(() => {
    if (getSessionToken()) {
      // Intentionally no-op: the parent restores the session/workspace state.
    }
  }, []);

  return (
    <div className="oa-login">
      <div className="oa-login__panel">
        <div className="oa-login__brand">
          <Logo size={52} tagline />
        </div>

        <h1 className="oa-login__heading">
          {mode === 'login' ? 'Sign in' : 'Create account'}
        </h1>
        <p className="oa-login__sub">
          {mode === 'login'
            ? 'Access your OPERANTA operations workspace.'
            : 'Set up your OPERANTA workspace.'}
        </p>

        <form className="oa-login__form" onSubmit={handleSubmit} noValidate>
          {mode === 'register' && (
            <>
              <label className="oa-field">
                <span className="oa-field__label">Business / Store name</span>
                <input
                  type="text"
                  className="oa-input"
                  placeholder="e.g. Acme Grocers"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  onBlur={() => mark('business_name')}
                  aria-invalid={touched.business_name && !businessValid}
                />
                {touched.business_name && !businessValid && (
                  <span className="oa-field__error">Business name is required.</span>
                )}
              </label>

              <label className="oa-field">
                <span className="oa-field__label">Full name</span>
                <input
                  type="text"
                  className="oa-input"
                  placeholder="Your full name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  onBlur={() => mark('full_name')}
                  aria-invalid={touched.full_name && !fullNameValid}
                />
                {touched.full_name && !fullNameValid && (
                  <span className="oa-field__error">Full name is required.</span>
                )}
              </label>
            </>
          )}

          <label className="oa-field">
            <span className="oa-field__label">Email address</span>
            <input
              type="email"
              className="oa-input"
              placeholder="you@company.com"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => mark('email')}
              aria-invalid={touched.email && !emailValid}
            />
            {touched.email && !emailValid && (
              <span className="oa-field__error">Enter a valid email address.</span>
            )}
          </label>

          <label className="oa-field">
            <span className="oa-field__label">Password</span>
            <div className="oa-password">
              <input
                type={showPassword ? 'text' : 'password'}
                className="oa-input"
                placeholder={mode === 'register' ? 'At least 10 characters' : 'Your password'}
                autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onBlur={() => mark('password')}
                aria-invalid={touched.password && (mode === 'register' ? !passwordValid : password.length === 0)}
              />
              <button
                type="button"
                className="oa-password__toggle"
                onClick={() => setShowPassword((s) => !s)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
            {touched.password && mode === 'register' && !passwordValid && (
              <span className="oa-field__error">Password must be at least 10 characters.</span>
            )}
            {touched.password && mode === 'login' && password.length === 0 && (
              <span className="oa-field__error">Password is required.</span>
            )}
          </label>

          {mode === 'register' && (
            <>
              <label className="oa-field">
                <span className="oa-field__label">Confirm password</span>
                <input
                  type={showPassword ? 'text' : 'password'}
                  className="oa-input"
                  placeholder="Re-enter your password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  onBlur={() => mark('confirm_password')}
                  aria-invalid={touched.confirm_password && !confirmValid}
                />
                {touched.confirm_password && !confirmValid && (
                  <span className="oa-field__error">Passwords do not match.</span>
                )}
              </label>

              <label className="oa-field">
                <span className="oa-field__label">Workspace access code (optional)</span>
                <input
                  type="text"
                  className="oa-input"
                  placeholder="Enter DEMO or leave blank"
                  value={accessCode}
                  onChange={(e) => setAccessCode(e.target.value)}
                />
                <span className="oa-field__hint">
                  Enter DEMO to access our existing hackathon demonstration workspace.
                  Leave blank to create your own workspace.
                </span>
              </label>
            </>
          )}

          {error && <div className="oa-login__error" role="alert">{error}</div>}

          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="oa-login__submit"
            loading={loading}
            disabled={mode === 'login' ? !loginValid : !registerValid}
          >
            {loading
              ? mode === 'login'
                ? 'Signing in…'
                : 'Creating account…'
              : mode === 'login'
                ? 'Sign in'
                : 'Create account'}
          </Button>
        </form>

        <div className="oa-login__toggle">
          {mode === 'login' ? (
            <button type="button" onClick={() => switchMode('register')}>
              No account? Create one
            </button>
          ) : (
            <button type="button" onClick={() => switchMode('login')}>
              Already have an account? Sign in
            </button>
          )}
        </div>

        <p className="oa-login__foot">
          Secured with signed sessions — no plaintext credentials are stored.
        </p>
      </div>
    </div>
  );
}
