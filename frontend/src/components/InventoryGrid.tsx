import React from 'react';
import { InventoryList, InventoryItem } from '../api/types';

interface InventoryGridProps {
  inventory: InventoryList | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function formatPrice(val: string | number | null): string {
  if (val === null || val === undefined) return '—';
  const num = typeof val === 'number' ? val : parseFloat(val);
  if (isNaN(num)) return '—';
  return `$${num.toFixed(2)}`;
}

/**
 * InventoryGrid Component — Omnichannel Inventory & Reorder Intelligence
 *
 * Features:
 * - Real database-backed inventory rows from GET /api/v1/inventory
 * - In-store vs. Online stock breakdown (on-hand vs available)
 * - Low-stock indicators and backend-driven reorder recommendation badges
 * - Responsive native CSS table layout with accessible attributes
 */
export const InventoryGrid: React.FC<InventoryGridProps> = ({
  inventory,
  loading,
  error,
  onRetry,
}) => {
  if (loading) {
    return (
      <article className="dashboard-card inventory-card" aria-busy="true">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">📦</span>
            <h3>Omnichannel Inventory & Stock Levels</h3>
          </div>
          <span className="badge badge-neutral">Loading Stock...</span>
        </div>
        <div className="card-skeleton table-skeleton">
          <div className="skeleton-row"></div>
          <div className="skeleton-row"></div>
          <div className="skeleton-row"></div>
        </div>
      </article>
    );
  }

  if (error) {
    return (
      <article className="dashboard-card inventory-card card-error-state" role="alert">
        <div className="card-header">
          <div className="card-title-group">
            <span className="card-icon" aria-hidden="true">📦</span>
            <h3>Omnichannel Inventory & Stock Levels</h3>
          </div>
          <span className="badge badge-danger">Sync Error</span>
        </div>
        <div className="card-error-body">
          <p className="card-error-message">{error}</p>
          <button className="btn-secondary" onClick={onRetry}>
            🔄 Retry Inventory Sync
          </button>
        </div>
      </article>
    );
  }

  const items: InventoryItem[] = inventory?.items || [];
  const lowStockCount = inventory?.low_stock_count ?? items.filter(i => i.reorder_recommended).length;

  return (
    <article className="dashboard-card inventory-card">
      <div className="card-header">
        <div className="card-title-group">
          <span className="card-icon" aria-hidden="true">📦</span>
          <div>
            <h3>Omnichannel Inventory & Stock Levels</h3>
            <span className="card-subtitle">Unified In-Store & Online Stock Synchronization</span>
          </div>
        </div>
        <div className="header-badges">
          {lowStockCount > 0 ? (
            <span className="badge badge-warning">
              ⚠️ {lowStockCount} Reorder {lowStockCount === 1 ? 'Alert' : 'Alerts'}
            </span>
          ) : (
            <span className="badge badge-success">✓ Stock Healthy</span>
          )}
          <span className="badge badge-neutral">
            {items.length} Active {items.length === 1 ? 'SKU' : 'SKUs'}
          </span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="empty-state">
          <p>No inventory records on file for this merchant.</p>
        </div>
      ) : (
        <div className="table-responsive-wrapper">
          <table className="data-table" aria-label="Omnichannel Inventory Table">
            <thead>
              <tr>
                <th scope="col">Product / SKU</th>
                <th scope="col">Category</th>
                <th scope="col" className="text-right">Retail / Cost</th>
                <th scope="col" className="text-center">In-Store (Avail/Total)</th>
                <th scope="col" className="text-center">Online (Avail/Total)</th>
                <th scope="col" className="text-center">Total Available</th>
                <th scope="col" className="text-center">Reorder Point</th>
                <th scope="col" className="text-center">Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  className={item.reorder_recommended ? 'row-highlight-warning' : ''}
                >
                  <td className="product-cell">
                    <span className="product-name">{item.product_name}</span>
                    <span className="product-sku">SKU: {item.sku}</span>
                  </td>
                  <td>
                    <span className="category-pill">{item.category}</span>
                  </td>
                  <td className="text-right">
                    <div className="price-group">
                      <strong className="retail-price">{formatPrice(item.retail_price)}</strong>
                      <span className="base-cost">Cost: {formatPrice(item.base_cost)}</span>
                    </div>
                  </td>
                  <td className="text-center">
                    <span className="stock-number">
                      <strong>{item.available_in_store}</strong> / {item.stock_in_store}
                    </span>
                    {item.reserved_in_store > 0 && (
                      <span className="reserved-hint">({item.reserved_in_store} reserved)</span>
                    )}
                  </td>
                  <td className="text-center">
                    <span className="stock-number">
                      <strong>{item.available_online}</strong> / {item.stock_online}
                    </span>
                    {item.reserved_online > 0 && (
                      <span className="reserved-hint">({item.reserved_online} reserved)</span>
                    )}
                  </td>
                  <td className="text-center">
                    <span className={`total-avail-badge ${item.reorder_recommended ? 'text-warning font-bold' : ''}`}>
                      {item.total_available} units
                    </span>
                    <span className="on-hand-sublabel">({item.total_on_hand} on hand)</span>
                  </td>
                  <td className="text-center">
                    <span className="reorder-threshold">{item.reorder_point}</span>
                    <span className="demand-hint">{item.daily_demand}/day</span>
                  </td>
                  <td className="text-center">
                    {item.reorder_recommended ? (
                      <span className="badge badge-warning pulse-subtle">
                        ⚠️ Reorder Needed
                      </span>
                    ) : (
                      <span className="badge badge-success">
                        Optimal
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
};
