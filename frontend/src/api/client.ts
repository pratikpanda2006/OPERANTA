import {
  HealthCheckResult,
  isHealthResponse,
  SpendEnvelope,
  isSpendEnvelope,
  InventoryList,
  isInventoryList,
  SupplierList,
  isSupplierList,
  OrderList,
  isOrderList,
  POSSaleRequestPayload,
  POSSaleResult,
  isPOSSaleResult,
  ReorderPlan,
  isReorderPlan,
  PurchaseOrder,
  isPurchaseOrder,
  PurchaseOrderList,
  isPurchaseOrderList,
  OwnerDecisionPayload,
  OwnerDecisionResult,
  isOwnerDecisionResult,
  OnlineCheckoutRequestPayload,
  OnlineCheckoutResult,
  isOnlineCheckoutResult,
  OnlineCaptureResult,
  isOnlineCaptureResult,
  AgentChatRequest,
  AgentChatResponse,
  isAgentChatResponse,
} from './types';
import {
  LoginResponse,
  isLoginResponse,
  RegisterRequestPayload,
  PayPalIntegrationStatus,
  isPayPalIntegrationStatus,
  PayPalIntegrationUpsertPayload,
} from './types';

/**
 * Resolves the backend API base URL from Vite environment variables,
 * defaulting to the local FastAPI development URL (http://127.0.0.1:8000).
 */
export function getApiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
}

// ---------------------------------------------------------------------------
// OPERANTA Session token storage
// ---------------------------------------------------------------------------

const SESSION_TOKEN_KEY = 'operanta_session_token';

export function getSessionToken(): string | null {
  try {
    return window.localStorage.getItem(SESSION_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setSessionToken(token: string | null): void {
  try {
    if (token) {
      window.localStorage.setItem(SESSION_TOKEN_KEY, token);
    } else {
      window.localStorage.removeItem(SESSION_TOKEN_KEY);
    }
  } catch {
    /* storage unavailable */
  }
}

export function logout(): void {
  setSessionToken(null);
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const baseUrl = getApiBaseUrl();
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/v1/auth/login`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new Error('Cannot reach the OPERANTA backend. Is the server running?');
  }

  if (response.status === 401) {
    throw new Error('Invalid email or password.');
  }
  if (!response.ok) {
    throw new Error(`Sign-in failed (HTTP ${response.status}).`);
  }

  let rawJson: unknown;
  try {
    rawJson = await response.json();
  } catch {
    throw new Error('Malformed response from the authentication service.');
  }
  if (!isLoginResponse(rawJson)) {
    throw new Error('Unexpected response from the authentication service.');
  }

  setSessionToken(rawJson.access_token);
  return rawJson;
}

/**
 * M15.0 — SaaS signup. Returns a LoginResponse with a freshly issued session,
 * so the user is signed in immediately after registering (no second login).
 */
export async function register(payload: RegisterRequestPayload): Promise<LoginResponse> {
  const baseUrl = getApiBaseUrl();
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/v1/auth/register`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      cache: 'no-cache',
    });
  } catch {
    throw new Error('Cannot reach the OPERANTA backend. Is the server running?');
  }

  if (!response.ok) {
    let detail = '';
    try {
      const body = await response.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      /* ignore */
    }
    if (response.status === 409) {
      throw new Error(detail || 'An account with this email already exists.');
    }
    if (response.status === 400) {
      throw new Error(detail || 'Registration failed. Please check your details.');
    }
    throw new Error(`Registration failed (HTTP ${response.status}).${detail ? ` ${detail}` : ''}`);
  }

  let rawJson: unknown;
  try {
    rawJson = await response.json();
  } catch {
    throw new Error('Malformed response from the authentication service.');
  }
  if (!isLoginResponse(rawJson)) {
    throw new Error('Unexpected response from the authentication service.');
  }
  setSessionToken(rawJson.access_token);
  return rawJson;
}

export async function getPaypalIntegrationStatus(): Promise<PayPalIntegrationStatus> {
  return requestEndpoint<PayPalIntegrationStatus>(
    'GET',
    '/api/v1/merchants/paypal-integration',
    isPayPalIntegrationStatus,
    'paypal integration status',
  );
}

export async function connectPaypalIntegration(
  payload: PayPalIntegrationUpsertPayload,
): Promise<PayPalIntegrationStatus> {
  return requestEndpoint<PayPalIntegrationStatus>(
    'POST',
    '/api/v1/merchants/paypal-integration',
    isPayPalIntegrationStatus,
    'paypal integration',
    payload,
  );
}

export async function disconnectPaypalIntegration(): Promise<PayPalIntegrationStatus> {
  return requestEndpoint<PayPalIntegrationStatus>(
    'DELETE',
    '/api/v1/merchants/paypal-integration',
    isPayPalIntegrationStatus,
    'paypal integration',
  );
}

/**
 * Generic HTTP request helper supporting GET and POST with validation and timeouts.
 */
async function requestEndpoint<T>(
  method: 'GET' | 'POST' | 'DELETE',
  path: string,
  validator: (data: unknown) => data is T,
  errorContext: string,
  body?: unknown,
  signal?: AbortSignal,
  timeoutMs: number = 8000
): Promise<T> {
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}${path}`;

  let timeoutController: AbortController | null = null;
  let effectiveSignal = signal;

  if (!signal) {
    timeoutController = new AbortController();
    effectiveSignal = timeoutController.signal;
    setTimeout(() => {
      timeoutController?.abort();
    }, timeoutMs);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Accept: 'application/json',
        ...(getSessionToken() ? { Authorization: `Bearer ${getSessionToken()}` } : {}),
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-cache',
      signal: effectiveSignal,
    });
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
    const message = err instanceof Error ? err.message : 'Network connection failed';
    throw new Error(`Cannot reach MarginGuard backend (${errorContext}): ${message}`);
  }

  const statusCode = response.status;

  if (!response.ok) {
    let errDetail = '';
    try {
      const errJson = await response.json();
      if (errJson && typeof errJson.detail === 'string') {
        errDetail = `: ${errJson.detail}`;
      }
    } catch {
      // ignore json parse error on error response
    }

    if (statusCode === 400) {
      throw new Error(`Validation Error (${errorContext})${errDetail}`);
    }
    if (statusCode === 404) {
      throw new Error(`The requested ${errorContext} resource was not found (HTTP 404).`);
    }
    if (statusCode === 403) {
      throw new Error(`Access restricted for ${errorContext} (HTTP 403 Forbidden).`);
    }
    if (statusCode >= 500) {
      throw new Error(`MarginGuard backend encountered an internal error while loading ${errorContext} (HTTP ${statusCode})${errDetail}`);
    }
    throw new Error(`Failed to process ${errorContext} (HTTP ${statusCode})${errDetail}`);
  }

  let rawJson: unknown;
  try {
    rawJson = await response.json();
  } catch {
    throw new Error(`Malformed JSON response received from backend for ${errorContext} (HTTP ${statusCode}).`);
  }

  if (!validator(rawJson)) {
    throw new Error(`Invalid response structure received from backend for ${errorContext}.`);
  }

  return rawJson;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function checkBackendHealth(signal?: AbortSignal): Promise<HealthCheckResult> {
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}/health`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-cache',
      signal,
    });
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    const message = err instanceof Error ? err.message : 'Network connection failed';
    throw new Error(`Cannot connect to MarginGuard backend at ${url} (${message})`);
  }

  const statusCode = response.status;
  if (!response.ok) {
    throw new Error(`Backend returned HTTP ${statusCode} (${response.statusText || 'Error'})`);
  }

  let rawJson: unknown;
  try {
    rawJson = await response.json();
  } catch {
    throw new Error(`Malformed JSON response received from backend (HTTP ${statusCode})`);
  }

  if (!isHealthResponse(rawJson)) {
    throw new Error(`Invalid health payload schema from backend (HTTP ${statusCode}).`);
  }

  return { data: rawJson, statusCode };
}

// ---------------------------------------------------------------------------
// Procurement & Spend Envelope
// ---------------------------------------------------------------------------

export async function fetchSpendEnvelope(signal?: AbortSignal): Promise<SpendEnvelope> {
  return requestEndpoint<SpendEnvelope>(
    'GET',
    '/api/v1/procurement/spend-envelope',
    isSpendEnvelope,
    'spend envelope',
    undefined,
    signal
  );
}

export async function fetchSuppliers(signal?: AbortSignal): Promise<SupplierList> {
  return requestEndpoint<SupplierList>(
    'GET',
    '/api/v1/procurement/suppliers',
    isSupplierList,
    'suppliers catalog',
    undefined,
    signal
  );
}

export async function fetchReorderPlan(signal?: AbortSignal): Promise<ReorderPlan> {
  return requestEndpoint<ReorderPlan>(
    'POST',
    '/api/v1/procurement/plan',
    isReorderPlan,
    'reorder planning',
    {},
    signal
  );
}

export async function createPurchaseOrder(
  payload: {
    supplier_id: string;
    items: Array<{ product_id: string; quantity: number; unit_cost: number }>;
    shipping_cost?: number;
    idempotency_key: string;
  },
  signal?: AbortSignal
): Promise<PurchaseOrder> {
  return requestEndpoint<PurchaseOrder>(
    'POST',
    '/api/v1/procurement/purchase-orders',
    isPurchaseOrder,
    'create purchase order',
    payload,
    signal
  );
}

export async function fetchPurchaseOrders(signal?: AbortSignal): Promise<PurchaseOrderList> {
  return requestEndpoint<PurchaseOrderList>(
    'GET',
    '/api/v1/procurement/purchase-orders',
    isPurchaseOrderList,
    'purchase orders list',
    undefined,
    signal
  );
}

export async function decidePurchaseOrder(
  poId: string,
  payload: OwnerDecisionPayload,
  signal?: AbortSignal
): Promise<OwnerDecisionResult> {
  return requestEndpoint<OwnerDecisionResult>(
    'POST',
    `/api/v1/procurement/purchase-orders/${poId}/decision`,
    isOwnerDecisionResult,
    'purchase order decision',
    payload,
    signal
  );
}

export async function agentChat(
  payload: AgentChatRequest,
  signal?: AbortSignal
): Promise<AgentChatResponse> {
  return requestEndpoint<AgentChatResponse>(
    'POST',
    '/api/v1/agent/chat',
    isAgentChatResponse,
    'business agent',
    payload,
    signal,
    240000
  );
}

// ---------------------------------------------------------------------------
// Inventory & Sales
// ---------------------------------------------------------------------------

export async function fetchInventory(signal?: AbortSignal): Promise<InventoryList> {
  return requestEndpoint<InventoryList>(
    'GET',
    '/api/v1/inventory',
    isInventoryList,
    'inventory data',
    undefined,
    signal
  );
}

export async function fetchOrders(signal?: AbortSignal): Promise<OrderList> {
  return requestEndpoint<OrderList>(
    'GET',
    '/api/v1/orders',
    isOrderList,
    'orders stream',
    undefined,
    signal
  );
}

export async function executePOSSale(
  payload: POSSaleRequestPayload,
  signal?: AbortSignal
): Promise<POSSaleResult> {
  return requestEndpoint<POSSaleResult>(
    'POST',
    '/api/v1/orders/pos',
    isPOSSaleResult,
    'POS sale transaction',
    payload,
    signal
  );
}

// ---------------------------------------------------------------------------
// M12.0 PayPal Sandbox customer checkout
// ---------------------------------------------------------------------------
export async function createOnlineCheckout(
  payload: OnlineCheckoutRequestPayload,
  signal?: AbortSignal
): Promise<OnlineCheckoutResult> {
  return requestEndpoint<OnlineCheckoutResult>(
    'POST',
    '/api/v1/orders/online-checkout',
    isOnlineCheckoutResult,
    'online checkout',
    payload,
    signal
  );
}

export async function captureOnlineOrder(
  orderId: string,
  signal?: AbortSignal
): Promise<OnlineCaptureResult> {
  return requestEndpoint<OnlineCaptureResult>(
    'POST',
    `/api/v1/orders/online-checkout/${encodeURIComponent(orderId)}/capture`,
    isOnlineCaptureResult,
    'online checkout capture',
    undefined,
    signal
  );
}
