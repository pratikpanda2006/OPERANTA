import { useState, useEffect, useRef, useCallback } from 'react';
import {
  checkBackendHealth,
  fetchSpendEnvelope,
  fetchInventory,
  fetchSuppliers,
  fetchOrders,
  fetchPurchaseOrders,
  getSessionToken,
  logout as apiLogout,
} from './api/client';
import type { AuthUser, ConnectionStatus, HealthCheckResult } from './api/types';
import { LoginScreen } from './components/LoginScreen';
import { SpendEnvelopeCard } from './components/SpendEnvelopeCard';
import { InventoryGrid } from './components/InventoryGrid';
import { SupplierMatrix } from './components/SupplierMatrix';
import { POSSimulator } from './components/POSSimulator';
import { PayPalCheckout } from './components/PayPalCheckout';
import { ProcurementEngine } from './components/ProcurementEngine';
import { AICommandCentre } from './components/AICommandCentre';
import { IntegrationPanel } from './components/IntegrationPanel';
import { Logo, Badge, Card, Button } from './components/ui';
import './App.css';

type Section = 'cockpit' | 'pos' | 'procurement' | 'ai' | 'settings';

const USER_STORAGE_KEY = 'operanta_user';

function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const n = typeof value === 'number' ? value : parseFloat(value);
  if (Number.isNaN(n)) return '—';
  return `$${n.toFixed(2)}`;
}

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [bootChecked, setBootChecked] = useState(false);
  const [section, setSection] = useState<Section>('cockpit');

  // Backend data
  const [healthStatus, setHealthStatus] = useState<ConnectionStatus>('loading');
  const [envelope, setEnvelope] = useState(null as any);
  const [envelopeLoading, setEnvelopeLoading] = useState(false);
  const [envelopeError, setEnvelopeError] = useState<string | null>(null);
  const [inventory, setInventory] = useState(null as any);
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const [inventoryError, setInventoryError] = useState<string | null>(null);
  const [suppliers, setSuppliers] = useState(null as any);
  const [suppliersLoading, setSuppliersLoading] = useState(false);
  const [suppliersError, setSuppliersError] = useState<string | null>(null);
  const [orders, setOrders] = useState(null as any);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [ordersError, setOrdersError] = useState<string | null>(null);
  const [purchaseOrders, setPurchaseOrders] = useState(null as any);
  const [poLoading, setPoLoading] = useState(false);
  const [poError, setPoError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const envelopeAbortRef = useRef<AbortController | null>(null);
  const inventoryAbortRef = useRef<AbortController | null>(null);
  const suppliersAbortRef = useRef<AbortController | null>(null);
  const ordersAbortRef = useRef<AbortController | null>(null);
  const poAbortRef = useRef<AbortController | null>(null);

  // Restore session on first paint
  useEffect(() => {
    const token = getSessionToken();
    if (token) {
      try {
        const stored = window.localStorage.getItem(USER_STORAGE_KEY);
        if (stored) setUser(JSON.parse(stored));
      } catch {
        /* ignore */
      }
    }
    setBootChecked(true);
  }, []);

  const loadHealth = useCallback(async () => {
    setHealthStatus('loading');
    try {
      const h: HealthCheckResult = await checkBackendHealth();
      setHealthStatus(h.statusCode < 500 ? 'connected' : 'error');
    } catch {
      setHealthStatus('error');
    }
  }, []);

  const loadSpendEnvelope = useCallback(async () => {
    if (envelopeAbortRef.current) envelopeAbortRef.current.abort();
    const controller = new AbortController();
    envelopeAbortRef.current = controller;
    setEnvelopeLoading(true);
    setEnvelopeError(null);
    try {
      setEnvelope(await fetchSpendEnvelope(controller.signal));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setEnvelopeError(err instanceof Error ? err.message : 'Failed to load spend envelope');
    } finally {
      setEnvelopeLoading(false);
    }
  }, []);

  const loadInventory = useCallback(async () => {
    if (inventoryAbortRef.current) inventoryAbortRef.current.abort();
    const controller = new AbortController();
    inventoryAbortRef.current = controller;
    setInventoryLoading(true);
    setInventoryError(null);
    try {
      setInventory(await fetchInventory(controller.signal));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setInventoryError(err instanceof Error ? err.message : 'Failed to load inventory');
    } finally {
      setInventoryLoading(false);
    }
  }, []);

  const loadSuppliers = useCallback(async () => {
    if (suppliersAbortRef.current) suppliersAbortRef.current.abort();
    const controller = new AbortController();
    suppliersAbortRef.current = controller;
    setSuppliersLoading(true);
    setSuppliersError(null);
    try {
      setSuppliers(await fetchSuppliers(controller.signal));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setSuppliersError(err instanceof Error ? err.message : 'Failed to load suppliers');
    } finally {
      setSuppliersLoading(false);
    }
  }, []);

  const loadOrders = useCallback(async () => {
    if (ordersAbortRef.current) ordersAbortRef.current.abort();
    const controller = new AbortController();
    ordersAbortRef.current = controller;
    setOrdersLoading(true);
    setOrdersError(null);
    try {
      setOrders(await fetchOrders(controller.signal));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setOrdersError(err instanceof Error ? err.message : 'Failed to load orders');
    } finally {
      setOrdersLoading(false);
    }
  }, []);

  const loadPurchaseOrders = useCallback(async () => {
    if (poAbortRef.current) poAbortRef.current.abort();
    const controller = new AbortController();
    poAbortRef.current = controller;
    setPoLoading(true);
    setPoError(null);
    try {
      setPurchaseOrders(await fetchPurchaseOrders(controller.signal));
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setPoError(err instanceof Error ? err.message : 'Failed to load purchase orders');
    } finally {
      setPoLoading(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    await Promise.allSettled([
      loadHealth(),
      loadSpendEnvelope(),
      loadInventory(),
      loadSuppliers(),
      loadOrders(),
      loadPurchaseOrders(),
    ]);
    setRefreshing(false);
  }, [loadHealth, loadSpendEnvelope, loadInventory, loadSuppliers, loadOrders, loadPurchaseOrders]);

  useEffect(() => {
    refreshAll();
    return () => {
      envelopeAbortRef.current?.abort();
      inventoryAbortRef.current?.abort();
      suppliersAbortRef.current?.abort();
      ordersAbortRef.current?.abort();
      poAbortRef.current?.abort();
    };
  }, [refreshAll]);

  function handleLoginSuccess(nextUser: AuthUser) {
    try {
      window.localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(nextUser));
    } catch {
      /* ignore */
    }
    setUser(nextUser);
    refreshAll();
  }

  function handleLogout() {
    apiLogout();
    try {
      window.localStorage.removeItem(USER_STORAGE_KEY);
    } catch {
      /* ignore */
    }
    setUser(null);
  }

  if (!bootChecked) {
    return (
      <div className="oa-boot">
        <Logo size={44} />
      </div>
    );
  }

  if (!user) {
    return <LoginScreen onSuccess={handleLoginSuccess} />;
  }

  const availableSpend =
    envelope && typeof envelope.available_spend === 'string'
      ? parseFloat(envelope.available_spend)
      : null;
  const weekCap =
    envelope && typeof envelope.demo_envelope_cap === 'string'
      ? parseFloat(envelope.demo_envelope_cap)
      : null;
  const committedSpend =
    envelope && typeof envelope.committed_spend === 'string'
      ? parseFloat(envelope.committed_spend)
      : null;
  const lowStock = inventory?.low_stock_count ?? 0;
  const orderCount = orders?.orders?.length ?? 0;
  const poPending = Array.isArray(purchaseOrders?.purchase_orders)
    ? purchaseOrders.purchase_orders.filter((p: any) => p.status === 'PENDING_APPROVAL').length
    : 0;

  const navItems: Array<{ id: Section; label: string; icon: string }> = [
    { id: 'cockpit', label: 'Cockpit Overview', icon: '◈' },
    { id: 'pos', label: 'POS & Sales', icon: '▤' },
    { id: 'procurement', label: 'Procurement & Approvals', icon: '⇄' },
    { id: 'ai', label: 'AI Command Centre', icon: '✦' },
    { id: 'settings', label: 'Workspace & Integrations', icon: '⚙' },
  ];

  return (
    <div className="oa-shell">
      <aside className="oa-sidebar">
        <div className="oa-sidebar__brand">
          <Logo size={38} />
        </div>
        <nav className="oa-nav">
          {navItems.map((item) => (
            <button
              key={item.id}
              className={`oa-nav__item ${section === item.id ? 'is-active' : ''}`}
              onClick={() => setSection(item.id)}
            >
              <span className="oa-nav__icon">{item.icon}</span>
              <span className="oa-nav__label">{item.label}</span>
              {item.id === 'ai' && <Badge tone="primary">AI</Badge>}
            </button>
          ))}
        </nav>
        <div className="oa-sidebar__foot">
          <div className="oa-user">
            <div className="oa-user__avatar">
              {user.display_name ? user.display_name[0].toUpperCase() : user.email[0].toUpperCase()}
            </div>
            <div className="oa-user__meta">
              <span className="oa-user__name">{user.display_name || user.email}</span>
              <span className="oa-user__role">{user.role}</span>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={handleLogout}>
            Sign out
          </Button>
        </div>
      </aside>

      <div className="oa-main">
        <header className="oa-topbar">
          <div className="oa-topbar__title">
            <h2>{navItems.find((n) => n.id === section)?.label}</h2>
            <p className="oa-topbar__subtitle">
              {section === 'cockpit' && 'Operational summary at a glance'}
              {section === 'pos' && 'In-store sales simulator & online checkout'}
              {section === 'procurement' && 'Suppliers, purchase orders and approvals'}
              {section === 'ai' && 'Ask your operations AI anything'}
              {section === 'settings' && 'Your workspace and optional PayPal Sandbox integration'}
            </p>
          </div>
          <div className="oa-topbar__right">
            <Badge tone={healthStatus === 'connected' ? 'success' : 'danger'}>
              {healthStatus === 'connected' ? 'Backend connected' : 'Backend offline'}
            </Badge>
            <Button size="sm" onClick={refreshAll} disabled={refreshing}>
              {refreshing ? 'Syncing…' : 'Refresh'}
            </Button>
          </div>
        </header>

        <main className={`oa-content ${section === 'ai' ? 'oa-content--fit' : ''}`}>
          {section === 'cockpit' && (
            <div className="oa-cockpit">
              {user && user.merchant_id === 'merchant_northstar' && (
                <div className="oa-demo-banner" role="status">
                  Hackathon Demo Workspace — <strong>Northstar Living</strong> (shared
                  with all demo users)
                </div>
              )}
              <div className="oa-statgrid">
                <div className="oa-stat oa-stat--primary oa-stat--hero">
                  <div className="oa-stat__top">
                    <span className="oa-stat__label">Available spend</span>
                  </div>
                  <div className="oa-stat__value">{money(availableSpend)}</div>
                  <div className="oa-stat__hint">
                    of {money(weekCap)} weekly procurement cap
                  </div>
                </div>
                <div className="oa-stat">
                  <div className="oa-stat__top"><span className="oa-stat__label">Weekly budget</span></div>
                  <div className="oa-stat__value">{money(weekCap)}</div>
                </div>
                <div className="oa-stat oa-stat--warning">
                  <div className="oa-stat__top"><span className="oa-stat__label">Committed spend</span></div>
                  <div className="oa-stat__value">{money(committedSpend)}</div>
                </div>
                <div className="oa-stat oa-stat--danger">
                  <div className="oa-stat__top"><span className="oa-stat__label">Replenishment alerts</span></div>
                  <div className="oa-stat__value">{lowStock}</div>
                  <div className="oa-stat__hint">items below reorder point</div>
                </div>
              </div>

              <div className="oa-cockpit__grid">
                <div className="oa-cockpit__primary">
                  <Card title="Weekly spend envelope" subtitle="Automated procurement guardrails" className="oa-card--grow">
                    <SpendEnvelopeCard envelope={envelope} loading={envelopeLoading} error={envelopeError} onRetry={loadSpendEnvelope} />
                  </Card>
                  <Card title="Inventory & reorder signals" subtitle={`${lowStock} item(s) below reorder point`}>
                    <InventoryGrid inventory={inventory} loading={inventoryLoading} error={inventoryError} onRetry={loadInventory} />
                  </Card>
                </div>
                <div className="oa-cockpit__side">
                  <Card title="Supplier network" subtitle="Allowlisted suppliers & offers">
                    <SupplierMatrix supplierData={suppliers} loading={suppliersLoading} error={suppliersError} onRetry={loadSuppliers} />
                  </Card>
                  <Card title="Pipeline">
                    <div className="oa-pipeline">
                      <div className="oa-pipeline__row">
                        <span>Orders this session</span>
                        <Badge tone="info">{orderCount}</Badge>
                      </div>
                      <div className="oa-pipeline__row">
                        <span>Purchase orders awaiting approval</span>
                        <Badge tone={poPending > 0 ? 'warning' : 'neutral'}>{poPending}</Badge>
                      </div>
                    </div>
                  </Card>
                </div>
              </div>
            </div>
          )}

          {section === 'pos' && (
            <div className="oa-pos">
              <div className="oa-pos__layout">
                <div className="oa-pos__col">
                  <Card title="POS Simulator" subtitle="In-store sales & inventory deduction">
                    <POSSimulator
                      inventory={inventory}
                      orders={orders}
                      ordersLoading={ordersLoading}
                      ordersError={ordersError}
                      onSaleCompleted={loadOrders}
                      onRefreshOrders={loadOrders}
                    />
                  </Card>
                </div>
                <div className="oa-pos__col">
                  <Card title="Online checkout" subtitle="PayPal Sandbox — redirected approval & capture">
                    <PayPalCheckout inventory={inventory} onSettled={loadOrders} />
                  </Card>
                </div>
              </div>
            </div>
          )}

          {section === 'procurement' && (
            <div className="oa-procurement">
              <Card title="Procurement & approvals" subtitle="Review suppliers, create drafts, approve with intent">
                <ProcurementEngine
                  inventory={inventory}
                  suppliers={suppliers}
                  purchaseOrders={purchaseOrders}
                  poLoading={poLoading}
                  poError={poError}
                  onPOUpdated={loadPurchaseOrders}
                />
              </Card>
            </div>
          )}

          {section === 'ai' && <AICommandCentre />}

          {section === 'settings' && user && (
            <div className="oa-settings">
              <IntegrationPanel merchantId={user.merchant_id} role={user.role} />
            </div>
          )}
        </main>

        <footer className="oa-footer">
          <span>© {new Date().getFullYear()} OPERANTA</span>
          <span className="oa-footer__tagline">Intelligence behind every operation</span>
        </footer>
      </div>
    </div>
  );
}
