import { FormEvent, useCallback, useEffect, useState } from 'react';
import {
  connectPaypalIntegration,
  disconnectPaypalIntegration,
  getPaypalIntegrationStatus,
} from '../api/client';
import type { PayPalIntegrationStatus } from '../api/types';
import { Button } from './ui';

const DEMO_MERCHANT_ID = 'merchant_northstar';

interface Props {
  merchantId: string;
  role: string;
}

export function IntegrationPanel({ merchantId, role }: Props) {
  const isDemo = merchantId === DEMO_MERCHANT_ID;

  const [status, setStatus] = useState<PayPalIntegrationStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Form state (normal SaaS merchants only)
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setStatusError(null);
    try {
      const s = await getPaypalIntegrationStatus();
      setStatus(s);
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : 'Could not load integration status.');
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  async function handleConnect(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setSavedMessage(null);
    if (!clientId.trim() || !clientSecret.trim()) {
      setFormError('Client ID and Client Secret are required.');
      return;
    }
    setSaving(true);
    try {
      const s = await connectPaypalIntegration({
        client_id: clientId.trim(),
        client_secret: clientSecret,
        environment: 'sandbox',
      });
      setStatus(s);
      setClientId('');
      setClientSecret('');
      setSavedMessage('PayPal Sandbox connected. Your credentials are stored encrypted on the server.');
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Connection failed.');
    } finally {
      setSaving(false);
    }
  }

  async function handleDisconnect() {
    setFormError(null);
    setSavedMessage(null);
    setSaving(true);
    try {
      const s = await disconnectPaypalIntegration();
      setStatus(s);
      setSavedMessage('PayPal Sandbox disconnected.');
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Disconnect failed.');
    } finally {
      setSaving(false);
    }
  }

  if (isDemo) {
    return (
      <div className="oa-card">
        <div className="oa-card__head">
          <h3 className="oa-card__title">Hackathon Demo Workspace</h3>
          <span className="oa-badge oa-badge--neutral">Shared demo</span>
        </div>
        <p className="oa-panel__text">
          You are exploring the existing <strong>Northstar Living</strong> demonstration
          workspace. It is shared by all demo users, so you may see activity from other
          demo visitors. Northstar&rsquo;s PayPal Sandbox configuration is managed centrally
          and is not editable here.
        </p>
        <div className="oa-panel__row">
          <span className="oa-panel__label">PayPal Sandbox</span>
          <span className="oa-panel__value">
            {status ? (status.connected ? 'Connected (shared configuration)' : 'Not connected') : '…'}
          </span>
        </div>
      </div>
    );
  }

  const canManage = role === 'owner';

  return (
    <div className="oa-card">
      <div className="oa-card__head">
        <h3 className="oa-card__title">Workspace &amp; Integration</h3>
        <span className="oa-badge oa-badge--neutral">Your own workspace</span>
      </div>

      <p className="oa-panel__text">
        This is your independent OPERANTA workspace. Your data is isolated from every
        other merchant. Optionally connect a PayPal Sandbox REST app to accept online
        checkout payments.
      </p>

      <div className="oa-panel__row">
        <span className="oa-panel__label">PayPal Sandbox</span>
        <span className="oa-panel__value">
          {status ? (status.connected ? `Connected (${status.client_id_masked ?? 'sandbox'})` : 'Not connected') : '…'}
        </span>
      </div>

      {statusError && <div className="oa-panel__error">{statusError}</div>}

      {savedMessage && <div className="oa-panel__success">{savedMessage}</div>}

      {status?.connected ? (
        canManage ? (
          <Button variant="ghost" size="sm" onClick={handleDisconnect} loading={saving}>
            Disconnect PayPal Sandbox
          </Button>
        ) : (
          <p className="oa-panel__hint">Only the workspace owner can manage PayPal credentials.</p>
        )
      ) : (
        canManage && (
          <form className="oa-panel__form" onSubmit={handleConnect}>
            <label className="oa-field">
              <span className="oa-field__label">Sandbox REST Client ID</span>
              <input
                type="text"
                className="oa-input"
                placeholder="AY… sandbox client id"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
              />
            </label>
            <label className="oa-field">
              <span className="oa-field__label">Sandbox REST Client Secret</span>
              <div className="oa-password">
                <input
                  type={showSecret ? 'text' : 'password'}
                  className="oa-input"
                  placeholder="Client secret (stored encrypted)"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                />
                <button
                  type="button"
                  className="oa-password__toggle"
                  onClick={() => setShowSecret((s) => !s)}
                >
                  {showSecret ? 'Hide' : 'Show'}
                </button>
              </div>
            </label>
            {formError && <div className="oa-panel__error">{formError}</div>}
            <Button type="submit" variant="primary" size="md" loading={saving}>
              Connect PayPal Sandbox
            </Button>
            <p className="oa-panel__hint">
              Your secret is validated against PayPal and stored encrypted on the server.
              It is never shown again.
            </p>
          </form>
        )
      )}
    </div>
  );
}
