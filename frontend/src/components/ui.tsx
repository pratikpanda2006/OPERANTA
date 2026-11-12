import { ButtonHTMLAttributes, ReactNode } from 'react';

/** Brand logo. Assumes /logo.png is served from the public directory. */
export function Logo({
  size = 40,
  tagline = false,
}: {
  size?: number;
  tagline?: boolean;
}) {
  return (
    <div className="oa-logo" style={{ gap: 12 }}>
      <img
        src="/logo.png"
        alt="OPERANTA logo"
        style={{ width: size, height: size, objectFit: 'contain' }}
      />
      <div className="oa-logo__wordmark">
        <span className="oa-logo__name">OPERANTA</span>
        {tagline && <span className="oa-logo__tagline">Intelligence behind every operation</span>}
      </div>
    </div>
  );
}

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success';

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  children,
  className = '',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
}) {
  return (
    <button
      className={`oa-btn oa-btn--${variant} oa-btn--${size} ${className}`}
      disabled={rest.disabled || loading}
      {...rest}
    >
      {loading && <span className="oa-spinner oa-spinner--btn" aria-hidden="true" />}
      <span>{children}</span>
    </button>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="oa-spinner-wrap" role="status">
      <span className="oa-spinner" aria-hidden="true" />
      {label && <span className="oa-spinner__label">{label}</span>}
    </div>
  );
}

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'primary';
  children: ReactNode;
}) {
  return <span className={`oa-badge oa-badge--${tone}`}>{children}</span>;
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
  padded = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={`oa-card ${className}`}>
      {(title || actions) && (
        <header className="oa-card__head">
          <div>
            {title && <h3 className="oa-card__title">{title}</h3>}
            {subtitle && <p className="oa-card__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="oa-card__actions">{actions}</div>}
        </header>
      )}
      <div className={padded ? 'oa-card__body' : 'oa-card__body oa-card__body--flush'}>{children}</div>
    </section>
  );
}

export function StatCard({
  label,
  value,
  icon,
  tone = 'neutral',
  hint,
  onClick,
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'primary';
  hint?: ReactNode;
  onClick?: () => void;
}) {
  const Comp = onClick ? 'button' : 'div';
  return (
    <Comp
      className={`oa-stat oa-stat--${tone}`}
      onClick={onClick}
      {...(onClick ? { type: 'button' as const } : {})}
    >
      <div className="oa-stat__top">
        <span className="oa-stat__label">{label}</span>
        {icon && <span className="oa-stat__icon">{icon}</span>}
      </div>
      <div className="oa-stat__value">{value}</div>
      {hint && <div className="oa-stat__hint">{hint}</div>}
    </Comp>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon?: ReactNode;
  title: string;
  body?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="oa-empty">
      {icon && <div className="oa-empty__icon">{icon}</div>}
      <h4 className="oa-empty__title">{title}</h4>
      {body && <p className="oa-empty__body">{body}</p>}
      {action && <div className="oa-empty__action">{action}</div>}
    </div>
  );
}

export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="oa-error-banner" role="alert">
      <div className="oa-error-banner__icon" aria-hidden="true">!</div>
      <p className="oa-error-banner__msg">{message}</p>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

/** Lightweight instrument mark used in sidebars/stat icons. */
export function KpiGlyph({ children }: { children: ReactNode }) {
  return <span className="oa-kpi-glyph">{children}</span>;
}
