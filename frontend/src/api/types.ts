/**
 * types.ts — Strong TypeScript contracts and runtime schema validation guards
 * for the MarginGuard API.
 *
 * Maps directly to backend Pydantic models:
 * - HealthResponse (app/schemas/health.py)
 * - SpendEnvelopeResponse (app/schemas/merchant.py)
 * - InventoryItemResponse & InventoryListResponse (app/schemas/inventory.py)
 * - SupplierOfferSummary, SupplierResponse & SupplierListResponse (app/schemas/supplier.py)
 * - POSSaleRequest, OrderResponse, OrderListResponse, POSSaleResponse (app/schemas/orders.py)
 * - ReorderPlanResponse, PurchaseOrderResponse, OwnerDecisionResponse (app/schemas/procurement.py)
 */

// ---------------------------------------------------------------------------
// 1. Health & Infrastructure Types
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  pilot_merchant: string;
}

export function isHealthResponse(data: unknown): data is HealthResponse {
  if (typeof data !== 'object' || data === null) {
    return false;
  }
  const r = data as Record<string, unknown>;
  return (
    typeof r.status === 'string' && r.status.trim().length > 0 &&
    typeof r.service === 'string' && r.service.trim().length > 0 &&
    typeof r.version === 'string' && r.version.trim().length > 0 &&
    typeof r.pilot_merchant === 'string' && r.pilot_merchant.trim().length > 0
  );
}

export type ConnectionStatus = 'loading' | 'connected' | 'error';

export interface HealthCheckResult {
  data: HealthResponse;
  statusCode: number;
}

// ---------------------------------------------------------------------------
// 2. Spend Envelope Types (GET /api/v1/procurement/spend-envelope)
// ---------------------------------------------------------------------------

export interface SpendEnvelope {
  merchant_id: string;
  demo_envelope_cap: string;         // Decimal as string, e.g. "500.00"
  max_order_cost_ceiling: string;    // Decimal as string, e.g. "150.00"
  committed_spend: string;           // Decimal as string, e.g. "175.00"
  available_spend: string;           // Decimal as string, e.g. "325.00"
  merchant_created_at: string;       // ISO 8601 timestamp string
  currency: string;                  // e.g. "USD"
}

export function isSpendEnvelope(data: unknown): data is SpendEnvelope {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.merchant_id === 'string' &&
    (typeof r.demo_envelope_cap === 'string' || typeof r.demo_envelope_cap === 'number') &&
    (typeof r.max_order_cost_ceiling === 'string' || typeof r.max_order_cost_ceiling === 'number') &&
    (typeof r.committed_spend === 'string' || typeof r.committed_spend === 'number') &&
    (typeof r.available_spend === 'string' || typeof r.available_spend === 'number') &&
    typeof r.currency === 'string'
  );
}

// ---------------------------------------------------------------------------
// 3. Inventory Types (GET /api/v1/inventory)
// ---------------------------------------------------------------------------

export interface InventoryItem {
  id: string;
  merchant_id: string;
  product_id: string;
  sku: string;
  product_name: string;
  category: string;
  retail_price: string;
  base_cost: string | null;
  stock_in_store: number;
  reserved_in_store: number;
  available_in_store: number;
  stock_online: number;
  reserved_online: number;
  available_online: number;
  total_on_hand: number;
  total_available: number;
  reorder_point: number;
  daily_demand: string;
  lead_days: number;
  safety_stock: number;
  reorder_recommended: boolean;
}

export interface InventoryList {
  merchant_id: string;
  items: InventoryItem[];
  total_items: number;
  low_stock_count: number;
}

export function isInventoryItem(data: unknown): data is InventoryItem {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.id === 'string' &&
    typeof r.product_id === 'string' &&
    typeof r.sku === 'string' &&
    typeof r.product_name === 'string' &&
    typeof r.category === 'string' &&
    typeof r.stock_in_store === 'number' &&
    typeof r.available_in_store === 'number' &&
    typeof r.total_available === 'number' &&
    typeof r.reorder_recommended === 'boolean'
  );
}

export function isInventoryList(data: unknown): data is InventoryList {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return Array.isArray(r.items) && r.items.every(isInventoryItem);
}

// ---------------------------------------------------------------------------
// 4. Supplier Types (GET /api/v1/procurement/suppliers)
// ---------------------------------------------------------------------------

export interface SupplierOfferSummary {
  offer_id: string;
  product_id: string;
  unit_cost: string;
  shipping_cost: string;
  lead_time_days: number;
  moq: number;
  is_illustrative: boolean;
}

export interface Supplier {
  id: string;
  merchant_id: string;
  name: string;
  contact_email: string | null;
  trust_tier: string;
  is_allowlisted: boolean;
  reliability_score: string;
  lead_time_days: number | null;
  is_illustrative: boolean;
  offers: SupplierOfferSummary[];
}

export interface SupplierList {
  merchant_id: string;
  suppliers: Supplier[];
  total_suppliers: number;
}

export function isSupplier(data: unknown): data is Supplier {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return typeof r.id === 'string' && typeof r.name === 'string' && typeof r.trust_tier === 'string';
}

export function isSupplierList(data: unknown): data is SupplierList {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return Array.isArray(r.suppliers) && r.suppliers.every(isSupplier);
}

// ---------------------------------------------------------------------------
// 5. Orders & POS Types (POST /api/v1/orders/pos & GET /api/v1/orders)
// ---------------------------------------------------------------------------

export interface OrderItem {
  id: string;
  product_id: string;
  product_name?: string;
  sku?: string;
  quantity: number;
  unit_price: string;
  line_total: string;
}

export interface Order {
  id: string;
  merchant_id: string;
  order_number: string;
  channel: string;
  status: string;
  subtotal: string;
  tax_amount: string;
  shipping_amount: string;
  total_amount: string;
  payment_method: string;
  created_at: string;
  items: OrderItem[];
}

export interface OrderList {
  merchant_id: string;
  orders: Order[];
  total_orders: number;
}

export interface POSSaleRequestPayload {
  items: Array<{ product_id: string; quantity: number }>;
  idempotency_key: string;
  payment_method?: string;
}

export interface POSSaleResult {
  order: Order;
  updated_inventory: Array<{
    product_id: string;
    stock_in_store: number;
    available_in_store: number;
    total_available: number;
  }>;
}

export function isOrder(data: unknown): data is Order {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.id === 'string' &&
    typeof r.order_number === 'string' &&
    (typeof r.total_amount === 'string' || typeof r.total_amount === 'number') &&
    Array.isArray(r.items)
  );
}

export function isOrderList(data: unknown): data is OrderList {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return Array.isArray(r.orders) && r.orders.every(isOrder);
}

export function isPOSSaleResult(data: unknown): data is POSSaleResult {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return isOrder(r.order) && Array.isArray(r.updated_inventory);
}

// ---------------------------------------------------------------------------
// 6. Procurement Planning & Purchase Orders Types
// ---------------------------------------------------------------------------

export interface SupplierEvalSummary {
  supplier_id: string;
  supplier_name: string;
  trust_tier: string;
  is_allowlisted: boolean;
  reliability_score: string;
  lead_time_days: number;
  moq: number;
  unit_cost: string;
  shipping_cost: string;
  landed_cost: string | null;
  trade_off_notes: string[];
}

export interface ReorderPlanItem {
  product_id: string;
  product_name: string;
  sku: string;
  available_stock: number;
  reorder_point: number;
  daily_demand: string;
  recommended_units: number;
  preferred_supplier: SupplierEvalSummary | null;
  alternative_suppliers: SupplierEvalSummary[];
}

export interface ReorderPlan {
  merchant_id: string;
  items: ReorderPlanItem[];
  total_recommendations: number;
}

export interface PolicyValidation {
  passed: boolean;
  reasons: string[];
  per_order_limit_passed: boolean;
  envelope_cap_passed: boolean;
  allowlist_passed: boolean;
  anti_splitting_passed: boolean;
  calculated_landed_cost: string | null;
  remaining_envelope_if_approved: string | null;
}

export interface PurchaseOrderItem {
  id: string;
  product_id: string;
  product_name?: string;
  sku?: string;
  quantity: number;
  unit_cost: string;
  line_total: string;
}

export interface PurchaseOrder {
  id: string;
  merchant_id: string;
  po_number: string;
  supplier_id: string;
  supplier_name?: string;
  status: string; // 'draft', 'pending_approval', 'ordered', 'rejected'
  item_subtotal: string;
  shipping_cost: string | null;
  tax_cost: string | null;
  labour_overhead_cost: string | null;
  total_landed_cost: string | null;
  has_unknown_costs: boolean;
  unknown_cost_nte_ceiling: string | null;
  payment_status: string;
  is_illustrative: boolean;
  created_at: string;
  updated_at: string;
  items: PurchaseOrderItem[];
  policy_evaluation?: PolicyValidation;
}

export interface PurchaseOrderList {
  merchant_id: string;
  purchase_orders: PurchaseOrder[];
  total_orders: number;
}

export interface OwnerDecisionPayload {
  decision: 'approved' | 'rejected';
  decided_by?: string;
  decision_reason: string;
  approval_type?: string;
  nte_ceiling?: number;
  idempotency_key: string;
}

export interface OwnerDecisionResult {
  purchase_order: PurchaseOrder;
  decision: string;
  spend_reserved: string;
  remaining_envelope_available: string;
  audit_logged: boolean;
}

export function isReorderPlan(data: unknown): data is ReorderPlan {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return Array.isArray(r.items);
}

export function isPurchaseOrder(data: unknown): data is PurchaseOrder {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return typeof r.id === 'string' && typeof r.po_number === 'string' && typeof r.status === 'string';
}

export function isPurchaseOrderList(data: unknown): data is PurchaseOrderList {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return Array.isArray(r.purchase_orders) && r.purchase_orders.every(isPurchaseOrder);
}

export function isOwnerDecisionResult(data: unknown): data is OwnerDecisionResult {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return isPurchaseOrder(r.purchase_order) && typeof r.decision === 'string';
}

// ---------------------------------------------------------------------------
// M12.0 PayPal Sandbox customer checkout
// ---------------------------------------------------------------------------
export interface OnlineCheckoutItemRequest {
  product_id: string;
  quantity: number;
}

export interface OnlineCheckoutRequestPayload {
  items: OnlineCheckoutItemRequest[];
}

export interface OnlineCheckoutItemResult {
  product_id: string;
  product_name: string;
  quantity: number;
  unit_price: number;
  line_total: number;
}

export interface OnlineCheckoutResult {
  merchant_id: string;
  order_id: string;
  order_number: string;
  paypal_order_id: string | null;
  approval_url: string | null;
  subtotal: number;
  tax_amount: number;
  total_amount: number;
  status: string;
  items: OnlineCheckoutItemResult[];
}

export interface OnlineCaptureResult {
  merchant_id: string;
  order_id: string;
  order_number: string;
  order_status: string;
  capture_status: string;
  paypal_capture_id: string;
  captured_amount: number;
  currency: string;
  settled: boolean;
  already_settled: boolean;
  revenue_ledger_id: number | null;
}

export function isOnlineCheckoutResult(data: unknown): data is OnlineCheckoutResult {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.order_id === 'string' &&
    typeof r.order_number === 'string' &&
    Array.isArray(r.items)
  );
}

export function isOnlineCaptureResult(data: unknown): data is OnlineCaptureResult {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.order_id === 'string' &&
    typeof r.paypal_capture_id === 'string' &&
    typeof r.captured_amount === 'number'
  );
}

// ---------------------------------------------------------------------------
// M13.0 Business Agent Types (maps to backend app/schemas/agent.py)
// ---------------------------------------------------------------------------

export interface AgentMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AgentChatRequest {
  message: string;
  history?: AgentMessage[];
}

export interface AgentToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: string | null;
}

export interface AgentToolBlock {
  name: string;
  reason: string;
}

export interface AgentChatResponse {
  answer: string;
  status: string;
  tool_calls: AgentToolCall[];
  tool_results: Array<Record<string, unknown>>;
  blocked: AgentToolBlock[];
  draft_reference?: string | null;
  approval_required: boolean;
  approval_reference?: string | null;
}

export function isAgentChatResponse(data: unknown): data is AgentChatResponse {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.answer === 'string' &&
    Array.isArray(r.tool_calls) &&
    Array.isArray(r.blocked) &&
    typeof r.approval_required === 'boolean'
  );
}

// ---------------------------------------------------------------------------
// OPERANTA Authentication Types (maps to backend app/schemas/auth.py)
// ---------------------------------------------------------------------------

export interface AuthUser {
  id: string;
  merchant_id: string;
  email: string;
  display_name?: string | null;
  role: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export function isAuthUser(data: unknown): data is AuthUser {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.id === 'string' &&
    typeof r.merchant_id === 'string' &&
    typeof r.email === 'string' &&
    typeof r.role === 'string'
  );
}

export function isLoginResponse(data: unknown): data is LoginResponse {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.access_token === 'string' &&
    typeof r.token_type === 'string' &&
    isAuthUser(r.user)
  );
}

/**
 * M15.0 — SaaS signup payload.
 * ``workspace_access_code`` blank => create a new isolated merchant workspace.
 * ``workspace_access_code`` === "DEMO" (validated server-side) => demo workspace.
 */
export interface RegisterRequestPayload {
  business_name: string;
  full_name: string;
  email: string;
  password: string;
  confirm_password: string;
  workspace_access_code?: string | null;
}

export interface PayPalIntegrationStatus {
  merchant_id: string;
  connected: boolean;
  environment?: string | null;
  client_id_masked?: string | null;
  webhook_id_masked?: string | null;
  verified_at?: string | null;
}

export interface PayPalIntegrationUpsertPayload {
  client_id: string;
  client_secret: string;
  environment?: 'sandbox' | 'live';
  webhook_id?: string | null;
}

export function isPayPalIntegrationStatus(data: unknown): data is PayPalIntegrationStatus {
  if (typeof data !== 'object' || data === null) return false;
  const r = data as Record<string, unknown>;
  return (
    typeof r.merchant_id === 'string' &&
    typeof r.connected === 'boolean'
  );
}
