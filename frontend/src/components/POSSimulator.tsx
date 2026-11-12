import React, { useState } from 'react';
import { InventoryList, OrderList } from '../api/types';
import { executePOSSale } from '../api/client';

interface POSSimulatorProps {
  inventory: InventoryList | null;
  orders: OrderList | null;
  ordersLoading: boolean;
  ordersError: string | null;
  onSaleCompleted: () => void;
  onRefreshOrders: () => void;
}

export const POSSimulator: React.FC<POSSimulatorProps> = ({
  inventory,
  orders,
  ordersLoading,
  ordersError,
  onSaleCompleted,
  onRefreshOrders,
}) => {
  const items = inventory?.items || [];
  const [selectedProductId, setSelectedProductId] = useState<string>(items[0]?.product_id || '');
  const [quantity, setQuantity] = useState<number>(2);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [feedbackMessage, setFeedbackMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Synchronize default select if not set
  React.useEffect(() => {
    if (!selectedProductId && items.length > 0) {
      setSelectedProductId(items[0].product_id);
    }
  }, [items, selectedProductId]);

  const selectedItem = items.find((i) => i.product_id === selectedProductId);

  const handleCheckout = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProductId) {
      setFeedbackMessage({ type: 'error', text: 'Please select a product.' });
      return;
    }
    if (quantity <= 0) {
      setFeedbackMessage({ type: 'error', text: 'Quantity must be positive.' });
      return;
    }

    setIsSubmitting(true);
    setFeedbackMessage(null);

    const idempotencyKey = `pos_sale_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;

    try {
      const result = await executePOSSale({
        items: [{ product_id: selectedProductId, quantity }],
        idempotency_key: idempotencyKey,
        payment_method: 'card_in_store',
      });

      setFeedbackMessage({
        type: 'success',
        text: `Sale completed! Order #${result.order.order_number} recorded ($${parseFloat(result.order.total_amount).toFixed(2)}). Stock decremented.`,
      });
      onSaleCompleted();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'POS checkout transaction failed';
      setFeedbackMessage({ type: 'error', text: msg });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="pos-simulator-container">
      <div className="pos-layout-grid">
        {/* Left Column: POS Terminal Form */}
        <article className="dashboard-card pos-terminal-card">
          <div className="card-header">
            <div className="card-title-group">
              <span className="card-icon" aria-hidden="true">💳</span>
              <div>
                <h3>Point of Sale (POS) Terminal</h3>
                <span className="card-subtitle">In-Store Register & Stock Deduction Simulator</span>
              </div>
            </div>
            <span className="badge badge-info">Register #1</span>
          </div>

          <form className="pos-form" onSubmit={handleCheckout}>
            <div className="form-group">
              <label htmlFor="pos-product-select">Select Boutique Product:</label>
              <select
                id="pos-product-select"
                className="form-control"
                value={selectedProductId}
                onChange={(e) => setSelectedProductId(e.target.value)}
                disabled={isSubmitting || items.length === 0}
              >
                {items.length === 0 && <option value="">No products loaded</option>}
                {items.map((item) => (
                  <option key={item.product_id} value={item.product_id}>
                    {item.product_name} (${parseFloat(item.retail_price).toFixed(2)}) — In Store: {item.available_in_store}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-row-2">
              <div className="form-group">
                <label htmlFor="pos-quantity">Units Sold:</label>
                <input
                  id="pos-quantity"
                  type="number"
                  min="1"
                  max={selectedItem ? selectedItem.available_in_store : 99}
                  className="form-control"
                  value={quantity}
                  onChange={(e) => setQuantity(parseInt(e.target.value, 10) || 1)}
                  disabled={isSubmitting}
                />
              </div>

              <div className="form-group">
                <label>Available On Floor:</label>
                <div className="stat-readonly">
                  <strong>{selectedItem ? selectedItem.available_in_store : 0}</strong> units
                </div>
              </div>
            </div>

            {/* Price Preview */}
            {selectedItem && (
              <div className="pos-pricing-summary">
                <div className="summary-row">
                  <span>Unit Price:</span>
                  <span>${parseFloat(selectedItem.retail_price).toFixed(2)}</span>
                </div>
                <div className="summary-row">
                  <span>Subtotal ({quantity} units):</span>
                  <span>${(parseFloat(selectedItem.retail_price) * quantity).toFixed(2)}</span>
                </div>
                <div className="summary-row">
                  <span>Tax (8% in-store):</span>
                  <span>${((parseFloat(selectedItem.retail_price) * quantity) * 0.08).toFixed(2)}</span>
                </div>
                <div className="summary-row total-row">
                  <strong>Estimated Total:</strong>
                  <strong>${((parseFloat(selectedItem.retail_price) * quantity) * 1.08).toFixed(2)}</strong>
                </div>
              </div>
            )}

            {feedbackMessage && (
              <div className={`alert-box alert-${feedbackMessage.type}`} role="alert">
                {feedbackMessage.text}
              </div>
            )}

            <button
              type="submit"
              className="btn-primary btn-checkout"
              disabled={isSubmitting || !selectedItem || selectedItem.available_in_store <= 0}
            >
              {isSubmitting ? 'Processing Transaction...' : '💳 Process In-Store Sale'}
            </button>
          </form>
        </article>

        {/* Right Column: Recent Omnichannel Orders Stream */}
        <article className="dashboard-card pos-orders-stream-card">
          <div className="card-header">
            <div className="card-title-group">
              <span className="card-icon" aria-hidden="true">🧾</span>
              <div>
                <h3>Recent Sales Orders</h3>
                <span className="card-subtitle">Omnichannel In-Store & Online Stream</span>
              </div>
            </div>
            <button className="btn-secondary btn-sm" onClick={onRefreshOrders} disabled={ordersLoading}>
              🔄 Refresh
            </button>
          </div>

          {ordersLoading && <div className="card-skeleton"><div className="skeleton-bar"></div></div>}

          {ordersError && (
            <div className="alert-box alert-error">
              {ordersError}
            </div>
          )}

          {!ordersLoading && orders && (
            <div className="orders-table-wrapper">
              {orders.orders.length === 0 ? (
                <p className="empty-notice">No orders recorded yet.</p>
              ) : (
                <table className="data-table compact-table">
                  <thead>
                    <tr>
                      <th>Order #</th>
                      <th>Channel</th>
                      <th>Items</th>
                      <th className="text-right">Total</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.orders.map((o) => (
                      <tr key={o.id}>
                        <td>
                          <strong>{o.order_number}</strong>
                          <span className="date-sublabel">{new Date(o.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                        </td>
                        <td>
                          <span className={`channel-badge channel-${o.channel}`}>
                            {o.channel === 'pos' ? '🏬 In-Store POS' : '🌐 Online'}
                          </span>
                        </td>
                        <td>
                          {o.items.map((i) => (
                            <span key={i.id} className="order-item-chip">
                              {i.quantity}× {i.product_name || i.product_id}
                            </span>
                          ))}
                        </td>
                        <td className="text-right font-bold text-accent">
                          ${parseFloat(o.total_amount).toFixed(2)}
                        </td>
                        <td>
                          <span className="badge badge-success">{o.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </article>
      </div>
    </section>
  );
};
