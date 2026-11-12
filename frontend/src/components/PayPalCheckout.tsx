import React, { useState } from 'react';
import {
  captureOnlineOrder,
  createOnlineCheckout,
} from '../api/client';
import type {
  InventoryItem,
  InventoryList,
  OnlineCaptureResult,
  OnlineCheckoutResult,
} from '../api/types';

interface PayPalCheckoutProps {
  inventory: InventoryList | null;
  onSettled?: () => void;
}

interface SelectedLine {
  product_id: string;
  qty: number;
}

/**
 * M12.0 PayPal Sandbox customer checkout.
 *
 * Flow: pick quantities -> create ONE internal order + PayPal order -> open the
 * PayPal approval URL -> after approval, capture + settle. The capture calls the
 * same canonical settlement used by the verified webhook.
 */
export const PayPalCheckout: React.FC<PayPalCheckoutProps> = ({ inventory, onSettled }) => {
  const [selected, setSelected] = useState<Record<string, number>>({});
  const [checkout, setCheckout] = useState<OnlineCheckoutResult | null>(null);
  const [capture, setCapture] = useState<OnlineCaptureResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const items = inventory?.items ?? [];

  const setQty = (pid: string, qty: number) => {
    setSelected((prev) => {
      const next = { ...prev };
      if (qty <= 0) delete next[pid];
      else next[pid] = qty;
      return next;
    });
    setCheckout(null);
    setCapture(null);
    setError(null);
  };

  const selectedLines: SelectedLine[] = Object.entries(selected)
    .map(([product_id, qty]) => ({ product_id, qty }))
    .filter((l) => l.qty > 0);

  const handleCreate = async () => {
    if (selectedLines.length === 0) return;
    setBusy(true);
    setError(null);
    setCheckout(null);
    setCapture(null);
    try {
      const payloadItems = selectedLines.map((l) => ({
        product_id: l.product_id,
        quantity: l.qty,
      }));
      const result = await createOnlineCheckout({ items: payloadItems });
      setCheckout(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create checkout');
    } finally {
      setBusy(false);
    }
  };

  const handleCapture = async () => {
    if (!checkout) return;
    setBusy(true);
    setError(null);
    try {
      const result = await captureOnlineOrder(checkout.order_id);
      setCapture(result);
      onSettled?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to capture payment');
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setSelected({});
    setCheckout(null);
    setCapture(null);
    setError(null);
  };

  const listable = items.filter((it: InventoryItem) => it.available_online > 0);

  return (
    <div style={styles.wrapper}>
      <h3 style={styles.title}>🧾 PayPal Sandbox Online Checkout</h3>
      <p style={styles.subtitle}>
        Create a real PayPal order, approve it in the sandbox, then capture the payment.
      </p>

      {error && <div style={styles.error}>{error}</div>}

      {!checkout && (
        <>
          <div style={styles.productList}>
            {listable.length === 0 && <div>No online-available inventory to sell.</div>}
            {listable.map((it: InventoryItem) => (
              <div key={it.product_id} style={styles.row}>
                <span style={styles.name}>
                  {it.product_name} <small>({it.sku})</small>
                </span>
                <span style={styles.price}>${it.retail_price}</span>
                <input
                  type="number"
                  min={0}
                  max={it.available_online}
                  value={selected[it.product_id] ?? 0}
                  onChange={(e) =>
                    setQty(it.product_id, Math.max(0, Number(e.target.value)))
                  }
                  style={styles.qtyInput}
                  aria-label={`Quantity for ${it.product_name}`}
                />
                <span style={styles.avail}>avail {it.available_online}</span>
              </div>
            ))}
          </div>
          <button
            style={styles.primaryBtn}
            disabled={busy || selectedLines.length === 0}
            onClick={handleCreate}
          >
            {busy ? 'Creating...' : 'Create PayPal Checkout'}
          </button>
        </>
      )}

      {checkout && !capture && (
        <div style={styles.card}>
          <div>
            <strong>Order:</strong> {checkout.order_number}{' '}
            <small>({checkout.order_id})</small>
          </div>
          <div>
            <strong>Total:</strong> ${Number(checkout.total_amount).toFixed(2)} USD
          </div>
          <div style={styles.actions}>
            {checkout.approval_url ? (
              <a
                href={checkout.approval_url}
                target="_blank"
                rel="noopener noreferrer"
                style={styles.approveBtn}
              >
                Approve in PayPal Sandbox
              </a>
            ) : (
              <div style={styles.error}>No approval URL returned by PayPal.</div>
            )}
            <button style={styles.primaryBtn} onClick={handleCapture} disabled={busy}>
              {busy ? 'Capturing...' : 'Capture Payment'}
            </button>
            <button style={styles.ghostBtn} onClick={reset}>
              Cancel / Reset
            </button>
          </div>
        </div>
      )}

      {capture && (
        <div style={styles.card}>
          <div>
            <strong>Capture status:</strong> {capture.capture_status}
          </div>
          <div>
            <strong>Paid:</strong> ${Number(capture.captured_amount).toFixed(2)} {capture.currency}
          </div>
          <div>
            <strong>Order state:</strong> {capture.order_status}
          </div>
          <div>
            <strong>Capture id:</strong> <code>{capture.paypal_capture_id}</code>
          </div>
          <div>
            <strong>Settled:</strong>{' '}
            {capture.settled ? 'Yes' : capture.already_settled ? 'Previously settled' : 'No'}
          </div>
          <button style={styles.ghostBtn} onClick={reset}>
            Start another checkout
          </button>
        </div>
      )}
    </div>
  );
};

// Minimal inline styling to avoid coupling to existing CSS class names.
// Dark theme matched to the app's existing design (dark panels, light text).
const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    border: '1px solid #2b3546',
    borderRadius: 10,
    padding: 16,
    marginTop: 18,
    background: '#151b26',
    color: '#dbe2ec',
  },
  title: { margin: '0 0 4px', fontSize: 16, color: '#e8ecf3' },
  subtitle: { margin: '0 0 12px', color: '#93a0b4', fontSize: 13 },
  error: { color: '#ff6b6b', marginBottom: 10, fontSize: 13 },
  productList: { maxHeight: 240, overflowY: 'auto', marginBottom: 12 },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '6px 0',
    borderBottom: '1px solid #262f3e',
  },
  name: { flex: 1, fontSize: 13, color: '#dbe2ec' },
  price: { width: 70, textAlign: 'right', color: '#7ec8f0', fontWeight: 600 },
  qtyInput: {
    width: 64,
    background: '#1e2635',
    color: '#dbe2ec',
    border: '1px solid #39455c',
    borderRadius: 4,
    padding: '4px 6px',
  },
  avail: { width: 64, textAlign: 'right', color: '#7d899b', fontSize: 12 },
  primaryBtn: {
    background: '#0a66c2',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    padding: '8px 14px',
    cursor: 'pointer',
    marginRight: 8,
  },
  approveBtn: {
    display: 'inline-block',
    background: '#ffc439',
    color: '#003087',
    fontWeight: 700,
    borderRadius: 6,
    padding: '8px 14px',
    textDecoration: 'none',
    marginRight: 8,
  },
  ghostBtn: {
    background: 'transparent',
    border: '1px solid #39455c',
    borderRadius: 6,
    padding: '8px 14px',
    cursor: 'pointer',
    color: '#dbe2ec',
  },
  card: {
    marginTop: 12,
    gap: 6,
    display: 'flex',
    flexDirection: 'column',
    color: '#dbe2ec',
  },
  actions: { display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 },
};
