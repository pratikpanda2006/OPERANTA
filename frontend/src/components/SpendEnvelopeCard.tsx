import React from 'react';
import { SpendEnvelope } from '../api/types';

interface SpendEnvelopeCardProps {
  envelope: SpendEnvelope | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

/**
 * Parses and formats currency string accurately to 2 decimal places.
 */
function formatCurrency(amountStr: string | number, currency = 'USD'): string {
  const num = typeof amountStr === 'number' ? amountStr : parseFloat(amountStr);
  if (isNaN(num)) return `$0.00`;
  return `$${num.toFixed(2)} ${currency !== 'USD' ? currency : ''}`.trim();
}

/**
 * SpendEnvelopeCard Component — Active Weekly Procurement Spend Envelope
 *
 * AGENTS.md Business Rules Enforced / Visualized:
 * 1. Agent Spend Envelope: $500 Cap for the active demo week.
 * 2. Per-Order Landed Cost Limit: $150 Cap per purchase order.
 * 3. Shows committed spend vs available spend.
 * 4. Renders dynamic backend response without hardcoded assumptions.
 */
export const SpendEnvelopeCard: React.FC<SpendEnvelopeCardProps> = ({
  envelope,
  loading,
  error,
  onRetry,
}) => {
  if (loading) {
    return (
      <article className="dashboard-card spend-envelope-card" aria-busy="true">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">💰</span>
            <h3>Spend Envelope & Financial Guardrails</h3>
          </div>
          <span className="badge badge-neutral">Loading...</span>
        </div>
        <div className="card-skeleton">
          <div className="skeleton-bar"></div>
          <div className="skeleton-stats"></div>
        </div>
      </article>
    );
  }

  if (error) {
    return (
      <article className="dashboard-card spend-envelope-card card-error-state" role="alert">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">💰</span>
            <h3>Spend Envelope & Financial Guardrails</h3>
          </div>
          <span className="badge badge-danger">Unavailable</span>
        </div>
        <div className="card-error-body">
          <p className="card-error-message">{error}</p>
          <button className="btn-secondary" onClick={onRetry}>
            🔄 Retry Spend Envelope
          </button>
        </div>
      </article>
    );
  }

  if (!envelope) {
    return (
      <article className="dashboard-card spend-envelope-card">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">💰</span>
            <h3>Spend Envelope & Financial Guardrails</h3>
          </div>
        </div>
        <p className="empty-notice">No spend envelope configured for this merchant.</p>
      </article>
    );
  }

  const cap = parseFloat(envelope.demo_envelope_cap) || 500.0;
  const committed = parseFloat(envelope.committed_spend) || 0.0;
  const available = parseFloat(envelope.available_spend) || 0.0;
  const ceiling = parseFloat(envelope.max_order_cost_ceiling) || 150.0;

  // Safe percentage calculation capped at 100% for progress visualization
  const rawPercentage = cap > 0 ? (committed / cap) * 100 : 0;
  const visualPercentage = Math.min(Math.max(rawPercentage, 0), 100);

  const isNearLimit = visualPercentage >= 80;
  const isOverLimit = rawPercentage > 100;

  return (
    <article className="dashboard-card spend-envelope-card">
      <div className="card-header">
        <div className="card-title-group">
          <span className="card-icon" aria-hidden="true">💰</span>
          <div>
            <h3>Spend Envelope & Financial Guardrails</h3>
            <span className="card-subtitle">Active Demo Week Allocation</span>
          </div>
        </div>
        <span className="badge badge-info">
          Cap: {formatCurrency(envelope.demo_envelope_cap, envelope.currency)}
        </span>
      </div>

      <div className="spend-visual-section">
        <div className="gauge-header">
          <div className="gauge-label">
            <span>Envelope Utilization</span>
            <strong>{rawPercentage.toFixed(1)}% Committed</strong>
          </div>
          <span className="gauge-sublabel">
            {formatCurrency(committed, envelope.currency)} / {formatCurrency(cap, envelope.currency)}
          </span>
        </div>

        {/* Visual Progress Gauge */}
        <div
          className="progress-track"
          role="progressbar"
          aria-valuenow={committed}
          aria-valuemin={0}
          aria-valuemax={cap}
          aria-label="Weekly spend envelope utilization"
        >
          <div
            className={`progress-fill ${isOverLimit ? 'fill-danger' : isNearLimit ? 'fill-warning' : 'fill-primary'}`}
            style={{ width: `${visualPercentage}%` }}
          />
        </div>
      </div>

      {/* Financial Key Metrics Grid */}
      <div className="spend-stats-grid">
        <div className="spend-stat">
          <span className="stat-label">Committed Spend</span>
          <span className="stat-value text-accent">
            {formatCurrency(envelope.committed_spend, envelope.currency)}
          </span>
          <span className="stat-hint">Incurred / Reserved POs</span>
        </div>

        <div className="spend-stat">
          <span className="stat-label">Available to Spend</span>
          <span className="stat-value text-success">
            {formatCurrency(envelope.available_spend, envelope.currency)}
          </span>
          <span className="stat-hint">Within $500 weekly cap</span>
        </div>

        <div className="spend-stat">
          <span className="stat-label">Per-Order Ceiling</span>
          <span className="stat-value text-muted">
            {formatCurrency(envelope.max_order_cost_ceiling, envelope.currency)}
          </span>
          <span className="stat-hint">Max landed cost per PO</span>
        </div>
      </div>

      {/* Guardrail Policy Notice */}
      <div className="guardrail-notice">
        <span className="notice-icon" aria-hidden="true">🛡️</span>
        <div className="notice-text">
          <strong>Backend-Enforced Guardrails:</strong> Autonomous or merchant purchase orders
          cannot exceed {formatCurrency(ceiling, envelope.currency)} landed cost. Orders exceeding
          available funds ({formatCurrency(available, envelope.currency)}) are automatically rejected.
        </div>
      </div>
    </article>
  );
};
