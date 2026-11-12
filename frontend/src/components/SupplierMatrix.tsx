import React from 'react';
import { SupplierList, Supplier } from '../api/types';

interface SupplierMatrixProps {
  supplierData: SupplierList | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function formatCurrency(val: string | number): string {
  const num = typeof val === 'number' ? val : parseFloat(val);
  if (isNaN(num)) return '$0.00';
  return `$${num.toFixed(2)}`;
}

/**
 * SupplierMatrix Component — Supplier Directory, Trust Tiers & Catalog Offers
 *
 * AGENTS.md Business Rules Enforced / Visualized:
 * 1. Supplier Allowlist & Trust Tiers:
 *    - Supplier A: Pre-approved & Allowlisted (Home décor & ceramics).
 *    - Supplier C: Pre-approved & Allowlisted (Kitchenware & dining).
 *    - Supplier B / New Suppliers: Requires Explicit Owner Approval.
 * 2. Mandatory "Illustrative Demo Data" tagging on demo suppliers & catalog ratings.
 * 3. Shows reliability scores, lead times, MOQ, and active offers.
 */
export const SupplierMatrix: React.FC<SupplierMatrixProps> = ({
  supplierData,
  loading,
  error,
  onRetry,
}) => {
  if (loading) {
    return (
      <article className="dashboard-card supplier-card" aria-busy="true">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">🏭</span>
            <h3>Supplier Evaluation Matrix & Catalog</h3>
          </div>
          <span className="badge badge-neutral">Loading Directory...</span>
        </div>
        <div className="card-skeleton supplier-skeleton">
          <div className="skeleton-card"></div>
          <div className="skeleton-card"></div>
          <div className="skeleton-card"></div>
        </div>
      </article>
    );
  }

  if (error) {
    return (
      <article className="dashboard-card supplier-card card-error-state" role="alert">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">🏭</span>
            <h3>Supplier Evaluation Matrix & Catalog</h3>
          </div>
          <span className="badge badge-danger">Sync Error</span>
        </div>
        <div className="card-error-body">
          <p className="card-error-message">{error}</p>
          <button className="btn-secondary" onClick={onRetry}>
            🔄 Retry Supplier Sync
          </button>
        </div>
      </article>
    );
  }

  const suppliers: Supplier[] = supplierData?.suppliers || [];

  return (
    <article className="dashboard-card supplier-card">
      <div className="card-header">
        <div className="card-title-group">
          <span className="card-icon" aria-hidden="true">🏭</span>
          <div>
            <h3>Supplier Evaluation Matrix & Catalog</h3>
            <span className="card-subtitle">Trust Tiers, Fulfillment Reliability & Landed Costs</span>
          </div>
        </div>
        <div className="header-badges">
          <span className="badge badge-illustrative" title="Demonstration supplier data">
            ℹ️ Illustrative Demo Data
          </span>
          <span className="badge badge-neutral">
            {suppliers.length} Qualified {suppliers.length === 1 ? 'Vendor' : 'Vendors'}
          </span>
        </div>
      </div>

      {suppliers.length === 0 ? (
        <div className="empty-state">
          <p>No supplier records found for this merchant.</p>
        </div>
      ) : (
        <div className="suppliers-grid">
          {suppliers.map((supplier) => {
            const isAllowlisted = supplier.trust_tier === 'allowlisted' || supplier.is_allowlisted;
            const reliability = parseFloat(supplier.reliability_score) || 0;

            return (
              <div
                key={supplier.id}
                className={`supplier-card-item ${isAllowlisted ? 'tier-allowlisted' : 'tier-pending-approval'}`}
              >
                <div className="supplier-card-top">
                  <div className="supplier-name-group">
                    <h4 className="supplier-name">{supplier.name}</h4>
                    {supplier.contact_email && (
                      <span className="supplier-email">{supplier.contact_email}</span>
                    )}
                  </div>

                  <span className={`badge ${isAllowlisted ? 'badge-success' : 'badge-warning'}`}>
                    {isAllowlisted ? '✓ Pre-Approved / Allowlisted' : '⚠️ Requires Approval'}
                  </span>
                </div>

                {/* Score & Lead-time metrics */}
                <div className="supplier-metrics-bar">
                  <div className="metric-col">
                    <span className="metric-label">Reliability</span>
                    <strong className={`metric-val ${reliability >= 90 ? 'text-success' : 'text-warning'}`}>
                      {reliability.toFixed(0)}%
                    </strong>
                  </div>

                  <div className="metric-col">
                    <span className="metric-label">Min Lead Time</span>
                    <strong className="metric-val text-main">
                      {supplier.lead_time_days !== null ? `${supplier.lead_time_days} days` : '—'}
                    </strong>
                  </div>

                  <div className="metric-col">
                    <span className="metric-label">Active Offers</span>
                    <strong className="metric-val text-muted">
                      {supplier.offers.length}
                    </strong>
                  </div>
                </div>

                {/* Offer catalog preview */}
                <div className="supplier-offers-list">
                  <span className="offers-title">Catalog Offerings:</span>
                  {supplier.offers.length === 0 ? (
                    <span className="no-offers-hint">No active product offers.</span>
                  ) : (
                    <ul className="offers-mini-table">
                      {supplier.offers.map((offer) => (
                        <li key={offer.offer_id} className="offer-row">
                          <span className="offer-cost">
                            Unit: <strong>{formatCurrency(offer.unit_cost)}</strong>
                          </span>
                          <span className="offer-shipping">
                            Freight: {formatCurrency(offer.shipping_cost)}
                          </span>
                          <span className="offer-moq">
                            MOQ: {offer.moq}
                          </span>
                          <span className="offer-lead">
                            {offer.lead_time_days}d dock
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Disclaimer pill */}
                <div className="supplier-disclaimer">
                  <span className="disclaimer-dot" aria-hidden="true"></span>
                  <span>Illustrative Demo Data — Simulated procurement history</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </article>
  );
};
