import React, { useState } from 'react';
import {
  InventoryList,
  SupplierList,
  PurchaseOrderList,
} from '../api/types';
import {
  createPurchaseOrder,
  decidePurchaseOrder,
} from '../api/client';

interface ProcurementEngineProps {
  inventory: InventoryList | null;
  suppliers: SupplierList | null;
  purchaseOrders: PurchaseOrderList | null;
  poLoading: boolean;
  poError: string | null;
  onPOUpdated: () => void;
}

export const ProcurementEngine: React.FC<ProcurementEngineProps> = ({
  inventory,
  suppliers,
  purchaseOrders,
  poLoading,
  poError,
  onPOUpdated,
}) => {
  const [selectedProductId, setSelectedProductId] = useState<string>('');
  const [selectedSupplierId, setSelectedSupplierId] = useState<string>('');
  const [orderQuantity, setOrderQuantity] = useState<number>(32);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [actionFeedback, setActionFeedback] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  const items = inventory?.items || [];
  const supplierItems = suppliers?.suppliers || [];

  // Default selections
  React.useEffect(() => {
    if (!selectedProductId && items.length > 0) {
      setSelectedProductId(items[0].product_id);
    }
    if (!selectedSupplierId && supplierItems.length > 0) {
      // Default to Supplier C if present (allowlisted preferred)
      const suppC = supplierItems.find((s) => s.id === 'sup_supplier_c');
      setSelectedSupplierId(suppC ? suppC.id : supplierItems[0].id);
    }
  }, [items, supplierItems, selectedProductId, selectedSupplierId]);

  const selectedSupplier = supplierItems.find((s) => s.id === selectedSupplierId);
  const selectedProduct = items.find((p) => p.product_id === selectedProductId);

  // Auto-find unit cost from supplier offers if available
  const activeOffer = selectedSupplier?.offers.find((o) => o.product_id === selectedProductId);
  const unitCost = activeOffer ? parseFloat(activeOffer.unit_cost) : (selectedProduct?.base_cost ? parseFloat(selectedProduct.base_cost) : 4.10);
  const shippingCost = activeOffer ? parseFloat(activeOffer.shipping_cost) : 8.00;
  const estimatedLanded = (orderQuantity * unitCost) + shippingCost;

  // Preset Blueprint Scenarios
  const loadBlueprintScenario = (units: number) => {
    setOrderQuantity(units);
    const suppC = supplierItems.find((s) => s.id === 'sup_supplier_c');
    if (suppC) setSelectedSupplierId(suppC.id);
    if (items.length > 0) setSelectedProductId(items[0].product_id);
    setActionFeedback(null);
  };

  const handleDraftPurchaseOrder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSupplierId || !selectedProductId) {
      setActionFeedback({ type: 'error', text: 'Select product and supplier.' });
      return;
    }

    setIsSubmitting(true);
    setActionFeedback(null);
    const idempotencyKey = `po_draft_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;

    try {
      const po = await createPurchaseOrder({
        supplier_id: selectedSupplierId,
        items: [{ product_id: selectedProductId, quantity: orderQuantity, unit_cost: unitCost }],
        shipping_cost: shippingCost,
        idempotency_key: idempotencyKey,
      });

      if (po.policy_evaluation && !po.policy_evaluation.passed) {
        setActionFeedback({
          type: 'warning',
          text: `POLICY BLOCKED: ${po.policy_evaluation.reasons.join(' | ')}. Order saved as REJECTED with NO funds reserved.`,
        });
      } else {
        setActionFeedback({
          type: 'success',
          text: `PO #${po.po_number} drafted successfully! Landed cost: $${parseFloat(po.total_landed_cost || '0').toFixed(2)}. Status: Pending Owner Approval.`,
        });
      }
      onPOUpdated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to draft purchase order';
      setActionFeedback({ type: 'error', text: msg });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDecision = async (poId: string, decision: 'approved' | 'rejected') => {
    setIsSubmitting(true);
    setActionFeedback(null);
    const idempotencyKey = `decision_${poId}_${Date.now()}`;

    try {
      const result = await decidePurchaseOrder(poId, {
        decision,
        decided_by: 'Northstar Store Owner',
        decision_reason: decision === 'approved' ? 'Stock reorder approved within policy limit' : 'Rejected by merchant owner',
        idempotency_key: idempotencyKey,
      });

      setActionFeedback({
        type: decision === 'approved' ? 'success' : 'warning',
        text: `Purchase Order #${result.purchase_order.po_number} ${decision.toUpperCase()}! ${
          decision === 'approved'
            ? `$${parseFloat(result.spend_reserved).toFixed(2)} reserved. New available envelope: $${parseFloat(result.remaining_envelope_available).toFixed(2)}.`
            : 'Order rejected. No funds reserved.'
        }`,
      });
      onPOUpdated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Decision execution failed';
      setActionFeedback({ type: 'error', text: msg });
    } finally {
      setIsSubmitting(false);
    }
  };

  const pos = purchaseOrders?.purchase_orders || [];
  const pendingOrders = pos.filter((p) => p.status === 'pending_approval');

  return (
    <section className="procurement-engine-section">
      <div className="procurement-grid">
        {/* Left Column: PO Proposal & Policy Simulator */}
        <article className="dashboard-card po-draft-card">
          <div className="card-header">
            <div className="card-title-group">
              <span className="card-icon" aria-hidden="true">🛡️</span>
              <div>
                <h3>Autonomous PO Generator & Policy Simulator</h3>
                <span className="card-subtitle">Enforces $150 Cap, Supplier Allowlist & 24h Anti-Splitting</span>
              </div>
            </div>
            <span className="badge badge-info">Deterministic Policy</span>
          </div>

          {/* Quick Blueprint Buttons */}
          <div className="blueprint-scenarios-bar">
            <span className="scenarios-label">Blueprint Scenarios:</span>
            <button
              type="button"
              className="btn-scenario btn-scenario-blocked"
              onClick={() => loadBlueprintScenario(42)}
              title="42 units * $4.10 + $8 shipping = $180.20 (Exceeds $150 cap)"
            >
              ⚠️ Test $180.20 (42 units → Blocked)
            </button>
            <button
              type="button"
              className="btn-scenario btn-scenario-pass"
              onClick={() => loadBlueprintScenario(32)}
              title="32 units * $4.10 + $8 shipping = $139.20 (Passes $150 cap)"
            >
              ✓ Test $139.20 (32 units → Passes)
            </button>
          </div>

          <form className="po-form" onSubmit={handleDraftPurchaseOrder}>
            <div className="form-row-2">
              <div className="form-group">
                <label>Select Product:</label>
                <select
                  className="form-control"
                  value={selectedProductId}
                  onChange={(e) => setSelectedProductId(e.target.value)}
                  disabled={isSubmitting}
                >
                  {items.map((i) => (
                    <option key={i.product_id} value={i.product_id}>
                      {i.product_name} ({i.sku})
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label>Select Supplier:</label>
                <select
                  className="form-control"
                  value={selectedSupplierId}
                  onChange={(e) => setSelectedSupplierId(e.target.value)}
                  disabled={isSubmitting}
                >
                  {supplierItems.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} [{s.trust_tier === 'allowlisted' ? 'Allowlisted' : 'Requires Approval'}]
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="form-row-3">
              <div className="form-group">
                <label>Units to Order:</label>
                <input
                  type="number"
                  min="1"
                  className="form-control"
                  value={orderQuantity}
                  onChange={(e) => setOrderQuantity(parseInt(e.target.value, 10) || 1)}
                  disabled={isSubmitting}
                />
              </div>

              <div className="form-group">
                <label>Unit Cost:</label>
                <div className="stat-readonly">${unitCost.toFixed(2)}</div>
              </div>

              <div className="form-group">
                <label>Freight / Ship:</label>
                <div className="stat-readonly">${shippingCost.toFixed(2)}</div>
              </div>
            </div>

            {/* Live Cost Computation Preview */}
            <div className={`cost-policy-preview ${estimatedLanded > 150 ? 'preview-blocked' : 'preview-valid'}`}>
              <div className="cost-row">
                <span>Calculated Landed Cost:</span>
                <strong className="cost-val">${estimatedLanded.toFixed(2)}</strong>
              </div>
              <div className="policy-verdict">
                {estimatedLanded > 150 ? (
                  <span className="text-danger font-bold">
                    ❌ Exceeds $150.00 Limit (Will be rejected by policy)
                  </span>
                ) : (
                  <span className="text-success font-bold">
                    ✓ Within $150.00 Limit (Eligible for Owner Approval)
                  </span>
                )}
              </div>
            </div>

            {actionFeedback && (
              <div className={`alert-box alert-${actionFeedback.type}`} role="alert">
                {actionFeedback.text}
              </div>
            )}

            <button
              type="submit"
              className="btn-primary btn-draft-po"
              disabled={isSubmitting}
            >
              {isSubmitting ? 'Evaluating Policy...' : '📋 Draft & Validate Purchase Order'}
            </button>
          </form>
        </article>

        {/* Right Column: Pending Owner Approvals & PO History */}
        <article className="dashboard-card po-approvals-card">
          <div className="card-header">
            <div className="card-title-group">
              <span className="card-icon" aria-hidden="true">✍️</span>
              <div>
                <h3>Owner Approval Queue</h3>
                <span className="card-subtitle">Human-In-The-Loop Sign-Off Before Spend Reservation</span>
              </div>
            </div>
            <span className="badge badge-warning">
              {pendingOrders.length} Pending
            </span>
          </div>

          {poLoading && <div className="card-skeleton"><div className="skeleton-bar"></div></div>}
          {poError && <div className="alert-box alert-error">{poError}</div>}

          {!poLoading && (
            <div className="approvals-queue-list">
              {pendingOrders.length === 0 ? (
                <div className="empty-notice">
                  <p>No purchase orders currently awaiting approval.</p>
                  <span className="text-dim">Draft an order on the left to test the approval loop.</span>
                </div>
              ) : (
                pendingOrders.map((po) => (
                  <div key={po.id} className="approval-card-item">
                    <div className="approval-top">
                      <div>
                        <strong className="po-title">{po.po_number}</strong>
                        <span className="supplier-subtitle">{po.supplier_name || po.supplier_id}</span>
                      </div>
                      <div className="approval-amount">
                        ${parseFloat(po.total_landed_cost || '0').toFixed(2)}
                      </div>
                    </div>

                    <div className="approval-items-preview">
                      {po.items.map((i) => (
                        <span key={i.id} className="order-item-chip">
                          {i.quantity} units @ ${parseFloat(i.unit_cost).toFixed(2)}
                        </span>
                      ))}
                    </div>

                    <div className="approval-actions">
                      <button
                        className="btn-success btn-sm"
                        onClick={() => handleDecision(po.id, 'approved')}
                        disabled={isSubmitting}
                      >
                        ✓ Approve & Reserve Funds
                      </button>
                      <button
                        className="btn-danger btn-sm"
                        onClick={() => handleDecision(po.id, 'rejected')}
                        disabled={isSubmitting}
                      >
                        ✗ Reject Order
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Historical PO Table */}
          <div className="po-history-section">
            <h4 className="sub-heading">All Purchase Orders:</h4>
            <div className="table-responsive-wrapper">
              <table className="data-table compact-table">
                <thead>
                  <tr>
                    <th>PO #</th>
                    <th>Supplier</th>
                    <th>Landed Cost</th>
                    <th>Status</th>
                    <th>Payment</th>
                  </tr>
                </thead>
                <tbody>
                  {pos.map((p) => (
                    <tr key={p.id}>
                      <td><strong>{p.po_number}</strong></td>
                      <td>{p.supplier_name || p.supplier_id}</td>
                      <td className="font-bold">${parseFloat(p.total_landed_cost || '0').toFixed(2)}</td>
                      <td>
                        <span className={`badge badge-${p.status === 'ordered' ? 'success' : p.status === 'rejected' ? 'danger' : 'warning'}`}>
                          {p.status}
                        </span>
                      </td>
                      <td>
                        <span className="text-dim">{p.payment_status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </article>
      </div>
    </section>
  );
};
