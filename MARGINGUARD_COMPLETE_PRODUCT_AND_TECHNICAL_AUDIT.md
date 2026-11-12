# MarginGuard — Complete Product, Feature & Technical Intelligence Audit

**Auditor:** Senior software architect / product analyst / business systems analyst / technical documentation specialist
**Scope:** Full `marginguard` repository (root: `C:\Users\PRATIK PANDA\Desktop\marginguard`)
**Audit type:** Read-only analysis. No source code changed, no dependencies installed, no configuration changed, no database touched, no external transactions triggered, no credentials exposed.
**Date/status:** Compiled from direct reading of actual implementation files (not only README/component names).

> **Primary intended use:** This document is the product-context input for a professional SaaS UI/UX redesign of MarginGuard. It must let another advanced model — **without repository access** — understand what MarginGuard is, what every screen does, how workflows operate, what data/APIs back them, which functionality must be protected, and which parts should be visually promoted or simplified.

---

# 1. Executive Product Summary

**MarginGuard** is a single-tenant (pilot), database-backed **autonomous retail operations cockpit** for a small boutique retailer ("Northstar Living"). It is a web application (React + TypeScript + Vite frontend, FastAPI + Python + PostgreSQL backend) that gives a store owner:

- **Real-time omnichannel inventory awareness** (in-store + online stock, reserved, available, reorder thresholds).
- **A spend-control layer** around purchasing: a weekly budget envelope, a per-order ceiling, a supplier allowlist, and anti-order-splitting protection.
- **A POS sales simulator** that posts real sales and reduces stock.
- **A PayPal Sandbox online checkout** that creates an order, takes the buyer through PayPal approval, and captures/settles the payment with webhook verification.
- **A replenishment planning engine** that recommends reorder quantities from inventory + sales velocity + supplier offers.
- **An owner approval workflow** for purchase orders that is enforced deterministically in the backend.
- **An "AI Command Centre"** — a Gemini-powered (with OpenRouter fallback) agent that can *read* inventory, velocity, suppliers, spend policy and reorder plans, and can *create unapproved drafts* and *surface drafts for owner approval*, but **cannot** approve, pay, refund, raise caps, or modify allowlists.

**The product solves:** the "many disconnected spreadsheets and tabs" problem of a small retailer that runs both a physical store and an online channel. It consolidates stock, sales, purchasing budgets, supplier offers and approvals into one place, and — unusually for a basic inventory dashboard — it **enforces the owner's financial policies in code** and uses an AI that is deliberately **read-mostly** so it cannot spend money on its own.

**What makes it different from a basic inventory dashboard:** the deterministic **financial guardrails** (envelope, ceiling, allowlist, anti-splitting) and the **guarded AI** that can only propose, never spend. The seller trusts the system because every money-moving action is either (a) a real, validated transaction, or (b) gated behind an explicit owner approval.

**Trustworthiness:** money actions are deterministic and validated; AI cannot bypass policy; tenant identity is server-derived; webhooks are verified; error messages are sanitized; amounts are decimal-precision.

**Current limitations (headline):** returns/refunds and PayPal disputes are **schema-only (NOT IMPLEMENTED)**; there is **no public storefront** (checkout is merchant-initiated from the cockpit); there is **no production authentication** (the app runs in `DEMO_MODE` with a fixed tenant); the database is seeded for **one pilot merchant** and effectively **one flagship product**; there is no scheduler/background worker (expired-reservation cleanup is opportunistic).

---

# 2. Complete Product Capabilities

## 2.1 Business-owner language

- **What problem:** A small retailer juggling a shop and an online store can't see, in one place, what's in stock, how fast things sell, how much they can safely spend this week, which supplier to buy from ( and who approved which order. Errors = overstocking, stockouts, overspending, or accidental duplicate orders.
- **Who uses it:** the store owner/manager ("the merchant") of Northstar Living; in this build, the *buyer* is simulated via the PayPal Sandbox checkout flow.
- **What the merchant does with it:**
  - Looks at the **Cockpit** to see spend remaining, stock levels, and suppliers at a glance.
  - Records a **POS sale** (simulator) and sees stock drop.
  - Runs a **customer checkout** with PayPal (sandbox) and sees the order complete.
  - Creates a **purchase order** for replenishment, sees whether it passes policy, and approves/rejects it.
  - Asks the **AI Command Centre** for inventory/supplier/policy analysis and to draft replenishment.
- **Automated:** stock decrement on sale, PayPal order/capture/webhook settlement, revenue ledger, inventory movements audit, spend-envelope math, reorder recommendation, policy checking.
- **Still human:** approving a purchase order (owner decision) — the only way funds are reserved. Also the merchant clicks "Capture" after a PayPal approval (though the webhook can also settle).
- **Why trustworthy:** the backend double-checks every spend against the envelope/ceiling/allowlist/anti-splitting; the AI cannot act on money; every action is audit-logged.

## 2.2 Technical/system language

- **Frontend:** React 18 + TypeScript + Vite, hand-rolled CSS design tokens (`frontend/src/index.css`, `frontend/src/App.css`), fetch-based API client with runtime response validators (`frontend/src/api/client.ts`, `frontend/src/api/types.ts`), 4 top-level tabs in `frontend/src/App.tsx`.
- **Backend:** FastAPI (async), `asyncpg` connection pool, Pydantic v2 models, layered `api/v1/endpoints -> services -> db/repositories`, deterministic SQL migrations runner, multi-tenant scoping via server-derived merchant id.
- **Database:** PostgreSQL; 20+ tables (see §10). Demo seed (migration 002) for Northstar Living.
- **AI:** Gemini (primary) → OpenRouter (secondary) with request-scoped sticky fallback; function-calling; strict argument validation; server-injected identity; tool profiles.
- **Payments:** PayPal REST Sandbox; create order → approve URL → capture → settle; verified webhooks.
- **Trust:** locked money flows, deterministic rules, audit log, sanitized errors, no secrets in logs.

---

# 3. Screen-by-Screen Functional Documentation

The frontend is a single-page app in `frontend/src/App.tsx` with a persistent header and **4 dashboard tabs** (`activeTab`): `overview`, `pos`, `procurement`, `ai`. There are no separate routes (no router library); tab switching is React state. A footer shows "MarginGuard Cockpit • Milestones 9–10".

## 3.1 Global shell & navigation

- **Purpose:** app frame, backend status, tab navigation.
- **User:** the merchant/owner.
- **Functionality:**
  - Header brand ("MarginGuard / AUTONOMOUS RETAIL COCKPIT").
  - Backend health status badge (`status-badge`): "Backend Online/Offline", derived from `checkBackendHealth()` → `GET /health`.
  - Four nav tabs (buttons `.nav-tab-btn`).
- **Interactions:** clicking a tab swaps `activeTab` and renders that tab's component; "Retry Connection" button re-runs health check.
- **Backend connection:** `GET /health` (liveness). On mount App loads: spend envelope, inventory, suppliers, orders, purchase orders (each with its own loading/error/retry state).
- **States:** `healthStatus`: 'connected' | 'error'; per-tab loading spinners and inline error bars.
- **Business rules:** none (shell).
- **Current limitations:** all data loads on mount regardless of active tab; backend-offline degrades the whole cockpit with unhelpful per-card errors.

## 3.2 Tab 1 — Cockpit Overview (`overview`)

Renders a `.dashboard-grid` with three cards.

### 3.2.1 SpendEnvelopeCard (`frontend/src/components/SpendEnvelopeCard.tsx`)
- **Purpose:** show the weekly procurement spend guardrail.
- **Data/API:** `GET /api/v1/procurement/spend-envelope` → `SpendEnvelope` (`available_spend`, `committed_spend`, `weekly_cap`, `currency`, `merchant_created_at`).
- **Functionality:** displays weekly cap, committed spend, remaining available, a usage gauge/bar; error + Retry.
- **Meaning:** `available_spend` = weekly_cap − committed (from ledger); seed = `$500` cap, `$175` committed, `$325` available.
- **States:** loading/empty/error/retry; card-error-state style.
- **Limitations:** read-only; no "top up/change cap" control; no breakdown by PO.

### 3.2.2 InventoryGrid (`frontend/src/components/InventoryGrid.tsx`)
- **Purpose:** omnichannel stock overview + reorder intelligence.
- **Data/API:** `GET /api/v1/inventory` → `InventoryList` of `InventoryItem`.
- **Functionality:** table of products with sku, retail price, in-store, online, reserved, total available, reorder point, and a reorder alert flag.
- **Meaning of fields (from `inventory_repo.list_inventory_items`):** `available_in_store/online` (generated columns), `total_available` = available_in_store + available_online (SQL expression), `reorder_recommended` = total_available ≤ reorder_point.
- **States:** loading/empty/error/retry; row highlight for reorder-alert SKUs.
- **Limitations:** read-only; no inline create-transfer action here (transfers are backend-API-supported but not surfaced in this tab).

### 3.2.3 SupplierMatrix (`frontend/src/components/SupplierMatrix.tsx`)
- **Purpose:** supplier directory, trust tiers, catalog offers.
- **Data/API:** `GET /api/v1/procurement/suppliers` → `SupplierList`.
- **Functionality:** lists suppliers with trust tier (allowlisted/etc.), category, and per-product offers (unit cost, lead time, landed cost estimate).
- **Meaning:** trust tier drives whether a supplier may be used without owner qualification.
- **States:** loading/empty/error/retry.
- **Limitations:** read-only; offer comparison is shown but actions live in Procurement and AI tabs.

## 3.3 Tab 2 — POS & Sales Simulator (`pos`)

Renders `<POSSimulator/>` then `<PayPalCheckout/>`.

### 3.3.1 POSSimulator (`frontend/src/components/POSSimulator.tsx`)
- **Purpose:** simulate a point-of-sale cash sale to move real stock.
- **Data/API:** `POST /api/v1/orders/pos` with `{ product_id, quantity }`. Uses `executePOSSale()`.
- **Functionality:** product dropdown (from inventory), quantity input (default 2), "Complete Sale" button; feedback message on success/error; refreshes orders (`onSaleCompleted` → `loadOrders`).
- **Business rules (backend, `order_repo.execute_pos_sale`):** validates product exists, quantity ≥ 1, stock sufficient; decrements stock; creates an order with `channel='pos'`, `status='completed'`; adds `order_items`; appends `inventory_movements`; runs in one transaction.
- **States:** initial (default first product), submitting, success banner, error banner, disabled while submitting.
- **Limitations:** simulator only (no barcode/cash-drawer); limited to products with inventory rows.

### 3.3.2 PayPalCheckout (`frontend/src/components/PayPalCheckout.tsx`)
- **Purpose:** merchant-initiated online checkout with the PayPal Sandbox.
- **Data/API:** `POST /api/v1/orders/online-checkout` (creates internal order + PayPal order), then `POST /api/v1/orders/online-checkout/{order_id}/capture`.
- **Functionality:** pick quantities per product → "Checkout with PayPal" → opens the PayPal approval URL (from `approve_url`) in a new tab → after approval, "Capture & settle" button calls capture. New `onSettled` reloads orders.
- **Backend flow (see §4 workflow 1):** create order → attach PayPal order id → capture → settle_capture (mark completed, ledger, stock, paypal_captures).
- **States:** product/line-item selection, submitting, awaiting-approval, capturing, settled, error.
- **Limitations:** there is no public storefront; the merchant drives the customer checkout; return page is read-only (never auto-captures).

## 3.4 Tab 3 — Procurement & Owner Approvals (`procurement`)

Renders `<ProcurementEngine/>` (`frontend/src/components/ProcurementEngine.tsx`).

- **Purpose:** create purchase orders and approve/reject them; see PO list.
- **Data/API:**
  - `GET /api/v1/procurement/suppliers` (for selection), `GET /api/v1/procurement/purchase-orders` (list).
  - `POST /api/v1/procurement/purchase-orders` (`createPurchaseOrder`) with supplier, items (product_id/quantity), and optional `has_unknown_costs`/`unknown_cost_nte_ceiling`.
  - `POST /api/v1/procurement/purchase-orders/{po_id}/decision` (`decidePurchaseOrder`) for approve/reject.
- **Functionality:** product + supplier dropdowns, quantity (default 32), "Create Purchase Order Draft"; inline feedback showing policy evaluation (pass/block + reasons, e.g. "$150 per-order cap", "spend envelope", "allowlist", "anti-splitting"); a purchase-order list with status and an approve/reject action per pending draft; approval confirmation.
- **Meaning of PO status (migration 001 `chk_po_status`):** `draft` → `pending_approval` → (owner decision) → `ordered` (approved, spend reserved) or `rejected`; fulfillment states `received`/`reconciled`/`cancelled` exist in schema. `payment_status` ∈ `unpaid`/`reserved`/`paid_sandbox`/`paid_demo`/`failed`; approval sets `payment_status='reserved'`.
- **States:** form loading, submitting, policy-block warning, success, error; pending list with actionable approve/reject.
- **Business rules enforced here:** full `evaluate_spending_policy` set (§7). Approval is the **only** place funds get reserved.
- **Limitations:** no monetary status detail (e.g., tracking number), no "ordered/received" fulfillment states beyond reservation.

## 3.5 Tab 4 — AI Command Centre (`ai`)

`frontend/src/components/AICommandCentre.tsx` (rewritten in the M13.0.8 polish). See §8 for the AI field.

- **Purpose:** natural-language business analysis and guarded drafting.
- **Data/API:** `POST /api/v1/agent/chat` → `AgentChatResponse` (`answer`, `status`, `tool_calls`, `blocked`, `draft_reference`, `approval_required`, `approval_reference`).
- **Functionality:** message composer + suggested prompts; each assistant bubble renders a **compact answer** (title, summary, up to 3 metric cards, recommendation, status pill) and a **"View supporting details"** toggle revealing inventory/replenishment/supplier/tool-execution details; if `approval_required` it shows the approval CTA (`decidePurchaseOrder`).
- **States:** empty, loading ("Agent is thinking…"), success, provider-error (status='error'), no-tool-calls.
- **Limitations:** answer quality depends on the live provider; compact metric cards are heuristically derived from tool output (no extra LLM call).

---

# 4. Complete Workflow Maps

## Workflow 1 — Online customer purchase (PayPal)

Sequence (implemented):

1. **Merchant initiates checkout** → `POST /api/v1/orders/online-checkout` (`create_checkout` in `payments.py`).
   - Validates request; calls `create_online_order` (`payment_repo.py`) which **creates an internal online order** (status presumably `pending`, `channel='online'`), reserves inventory, and opportunistically calls `release_expired_reservations`.
   - Builds PayPal order payload (`build_paypal_order_payload`), request id `mg-cr-{merchant}-{order_id}`.
   - Calls PayPal `create_order` **outside** any DB transaction/lock.
   - **Definite provider failure** (PayPalAPIError/PayPalAuthError) → `cancel_online_order_and_release` (release reservation), HTTP 502.
   - **Ambiguous failure** (timeout/transport) → keep order pending + reservation, HTTP 503 (reconcilable).
   - Success → stores `paypal_order_id` (`attach_paypal_order`), returns `approve_url`.
2. **Buyer approves on PayPal** → returns to `GET /api/v1/orders/online-checkout/{order_id}/return` (read-only; token must match stored `paypal_order_id`; **never auto-captures**).
3. **Capture** → either webhook `PAYMENT.CAPTURE.COMPLETED` (`POST /api/v1/webhooks/paypal`) or merchant `POST /api/v1/orders/online-checkout/{order_id}/capture`.
   - Webhook path: verify signature/event id, claim event id idempotently, `_process_capture_completed` → `settle_capture`.
   - Capture endpoint: calls PayPal capture; handles `ORDER_ALREADY_CAPTURED` by reconciling with local `paypal_captures`.
4. **Settlement** (`settle_capture` in `payment_repo.py`): update order status `completed`, insert `paypal_captures` row, append `financial_ledger` **revenue** entry, decrement online stock, record movements. Idempotent.
5. **Cancel:** `GET/POST /api/v1/orders/online-checkout/{order_id}/cancel` → `_cancel_pending_checkout` releases reservation exactly once; rejects already-captured orders.

## Workflow 2 — POS and inventory

- `POST /api/v1/orders/pos` → `execute_pos_sale` (`order_repo.py`): validate product/quantity/stock → create order (`channel='pos'`, `status='completed'`) + `order_items` → decrement stock in `inventory_items` → append `inventory_movements` (reason `pos_sale`) → append ledger revenue. Single transaction.
- **Velocity:** `get_sales_velocity` tool / `list_inventory_items` exposes `daily_demand` (a configured estimate, not derived from actual sales).
- **Replenishment requirement:** `total_available <= reorder_point` → `reorder_recommended`; `generate_reorder_plan` (`procurement_repo.py`) builds the plan for at-or-below-ROP SKUs.

## Workflow 3 — Procurement and approvals

1. Reorder recommendation (AI `calculate_reorder_plan` or `GET /api/v1/procurement/plan`).
2. Supplier compare (AI `compare_suppliers` or supplier matrix).
3. Create PO: `POST /api/v1/procurement/purchase-orders` → `create_purchase_order` validates policy (`evaluate_spending_policy`) → inserts draft.
4. Budget validation: landed cost, per-order $150, envelope $500, allowlist, anti-splitting (rolling 24h).
5. Owner approval: `POST .../purchase-orders/{po_id}/decision` → `process_owner_decision` (`procurement_repo.py`): lock merchant then PO rows; **re-validate** policy; on approve insert `spend_reservations` + ledger entry, set status `ordered`, payment_status `reserved`; on reject status `rejected`; record approvals + audit log.
6. **Payment/procurement state beyond reservation:** NOT IMPLEMENTED (no "ordered→received", no actual supplier payment; PayPal is customer-facing only).

## Workflow 4 — Returns and refunds

**NOT IMPLEMENTED.** Tables exist (`order_refunds`, `paypal_captures.amount_refunded`, `orders.status='refunded'/'partially_refunded'`, `paypal_captures.status` refund values) but there are **no** backend endpoints, services, or UI for creating/approving/executing a refund.

## Workflow 5 — Disputes

**NOT IMPLEMENTED.** `dispute_cases` / `dispute_evidence` tables exist; no code reads/writes them.

## Workflow 6 — AI Command Centre

- **Input/conversation:** `POST /api/v1/agent/chat` with `message`, `history` (last 10), `tool_profile`.
- **LLM provider:** Gemini primary (`gemini-3.8-flash`); OpenRouter (`openrouter/free`) fallback on Gemini timeout/provider error; **sticky** per request; fresh provider per HTTP request (Gemini primary again). Implemented in `agent/provider.py` (`FallbackProvider`).
- **Tools (registry `TOOL_EXECUTORS` in `agent/tools.py`):**
  - `get_dashboard_summary` — reads envelope + inventory counts. Read-only.
  - `get_inventory` — reads inventory. Read-only.
  - `get_sales_velocity` — reads `daily_demand`/reorder params. Read-only.
  - `calculate_reorder_plan` — deterministic reorder plan. Read-only.
  - `compare_suppliers` — deterministic supplier ranking for a product. Read-only.
  - `check_spend_policy` — runs `evaluate_spending_policy`. Read-only evaluation.
  - `create_purchase_order_draft` — creates an **UNAPPROVED** draft; only after explicit user request (intent regex `_EXPLICIT_DRAFT_RE`); gated by `is_explicit_draft_request`.
  - `request_approval` — surfaces an existing draft for owner approval; never approves.
- **Safety:** strict argument models (`TOOL_ARG_MODELS` via `TypeAdapter`); `sanitize_tool_args` strips `merchant_id/actor_id/decided_by/approval_identity/owner_id`; identities server-injected; agent cannot approve/pay/refund/raise caps/modify allowlist; profiles `standard` (all) vs `inventory_only` (only `get_inventory`); unknown tools rejected; service loop bounded to `MAX_TOOL_ITERATIONS=6`.
- **Display:** executed on frontend as formatted tables/lists (no raw JSON blobs); errors sanitized.

---

# 5. Feature Inventory and Implementation Status

| Feature | Screen | What it does | Data source/API | User action | Status | Limitation |
|---|---|---|---|---|---|---|
| Backend health | Global header | liveness badge + retry | `GET /health` | Retry button | Fully implemented | — |
| Spend envelope gauge | Cockpit | show cap/committed/available | `GET /procurement/spend-envelope` | none (read) | Fully implemented | read-only |
| Inventory grid | Cockpit | stock + reorder alerts | `GET /inventory` | none (read) | Fully implemented | no inline transfer |
| Supplier matrix | Cockpit | suppliers + tiers + offers | `GET /procurement/suppliers` | none (read) | Fully implemented | read-only |
| Inventory transfer | Backend only | move stock in-store↔online | `POST /inventory/transfers` | API only | Backend implemented, **not exposed in UI** | no UI |
| POS sale | POS tab | record sale, drop stock | `POST /orders/pos` | dropdowns + Complete Sale | Fully implemented | simulator |
| Order list | POS tab | show orders | `GET /orders` | refresh | Fully implemented | — |
| Online checkout | POS tab | PayPal order + approve URL | `POST /orders/online-checkout` | purchase quantities + Checkout | Fully implemented | merchant-initiated |
| Capture/settle | POS tab + webhook | capture + settle | `POST .../capture`, webhook | Capture button | Fully implemented | return page never auto-captures |
| Webhook processing | backend | verify + settle + idempotent | `POST /webhooks/paypal` | none | Fully implemented | only COMPLETED/DENIED handled |
| Reorder plan | Procurement/AI | replenishment recommendation | `POST /procurement/plan`, AI `calculate_reorder_plan` | view | Fully implemented | plan not surfaced as a dedicated UI table |
| Purchase order create | Procurement tab | create draft + policy check | `POST /procurement/purchase-orders` | form + Create | Fully implemented | — |
| Purchase order list | Procurement tab | statuses | `GET /procurement/purchase-orders` | refresh | Fully implemented | — |
| Owner approve/reject | Procurement tab + AI | decision + reserve | `POST .../purchase-orders/{id}/decision` | Approve/Reject | Fully implemented | only this reserves funds |
| AI chat | AI tab | analysis + guarded drafting | `POST /agent/chat` | chat | Fully implemented | answer quality = provider |
| AI compact answer + details | AI tab | concise summary + expandable details | same | View supporting details | Fully implemented | — |
| Refund | — | — | (tables only) | — | **Not implemented** | schema only |
| Dispute | — | — | (tables only) | — | **Not implemented** | schema only |
| Storefront | — | — | — | — | **Not implemented** | merchant-driven checkout |
| Production auth | — | — | `DEMO_MODE` | — | **Not implemented** | fixed tenant |
| Background scheduler | — | reservation cleanup | `release_expired_reservations` | — | Partially implemented | opportunistic only, no cron |

---

# 6. API and Data Mapping

All routes are mounted under `prefix="/api/v1"` (`backend/app/api/v1/router.py`). Router prefixes: `/inventory`, `/orders`, `/orders/online-checkout`, `/webhooks`, `/procurement`, `/agent`.

**Verifiable endpoints:**

| Method | Path | Endpoint function |
|---|---|---|
| GET | `/health` | `main.py` liveness |
| GET | `/health/ready` | readiness (DB ping) |
| GET | `/api/v1/inventory` | `inventory.py:27` |
| POST | `/api/v1/inventory/transfers` | `inventory.py:54` |
| POST | `/api/v1/orders/pos` | `orders.py:25` |
| GET | `/api/v1/orders` | `orders.py:61` |
| POST | `/api/v1/orders/online-checkout` | `payments.py:267` |
| POST | `/api/v1/orders/online-checkout/{order_id}/capture` | `payments.py:368` |
| GET | `/api/v1/orders/online-checkout/{order_id}/return` | `payments.py:610` |
| GET | `/api/v1/orders/online-checkout/{order_id}/cancel` | `payments.py:629` |
| POST | `/api/v1/orders/online-checkout/{order_id}/cancel` | `payments.py:642` |
| POST | `/api/v1/webhooks/paypal` | `payments.py:472` |
| GET | `/api/v1/procurement/spend-envelope` | `procurement.py:38` |
| GET | `/api/v1/procurement/suppliers` | `procurement.py:65` |
| POST | `/api/v1/procurement/plan` | `procurement.py:89` |
| POST | `/api/v1/procurement/purchase-orders` | `procurement.py:113` |
| GET | `/api/v1/procurement/purchase-orders` | `procurement.py:146` |
| POST | `/api/v1/procurement/purchase-orders/{po_id}/decision` | `procurement.py:170` |
| POST | `/api/v1/agent/chat` | `agent.py:68` |

**Repositories (data access):** `inventory_repo.py`, `merchant_repo.py` (`get_spend_envelope`), `order_repo.py` (`execute_pos_sale`, `list_orders`, `get_order_by_id`), `payment_repo.py` (`create_online_order`, `attach_paypal_order`, `cancel_online_order_and_release`, `settle_capture`, `release_expired_reservations`), `procurement_repo.py` (`generate_reorder_plan`, `evaluate_spending_policy`, `create_purchase_order`, `process_owner_decision`), `supplier_repo.py`.

**Database tables (migration 001):** `schema_migrations`, `merchants`, `products`, `suppliers`, `supplier_offers`, `inventory_items`, `inventory_movements`, `procurement_requests`, `purchase_orders`, `purchase_order_items`, `spend_reservations`, `purchase_order_approvals`, `financial_ledger`, `orders`, `order_items`, `paypal_captures`, `order_refunds`, `dispute_cases`, `dispute_evidence`, `webhook_events`, `operational_audit_log`. Migrations 003 (PO landed-cost constraint) and 004 (PayPal online checkout columns).

**Status enums (from schema CHECKs):** `orders.status` ∈ completed/cancelled/refunded/partially_refunded; `purchase_orders.status` (draft/…/ordered/rejected — see `chk_po_status`), `payment_status` ∈ …/reserved; `spend_reservations.status` ∈ active/committed/released/expired; `webhook_events.status` ∈ received/processing/processed/failed; `paypal_captures.status` ∈ completed/pending/refunded/partially_refunded.

---

# 7. Business Rules and Safety Architecture

| Rule | Why | Where | Violation result | AI-bypass? | Human approval? |
|---|---|---|---|---|---|
| Landed cost must be explicit | avoid hidden/zeroed costs | `evaluate_spending_policy` | unknown costs => `total_landed_cost=NULL`, ceiling used | No | — |
| Per-order ceiling $150 | cap single PO | `evaluate_spending_policy` | block + reasons | No | — |
| Weekly spend envelope $500 | cap weekly commitments | ledger-derived `available_spend` | block if effective cost > available | No | — |
| Supplier allowlist (trust_tier='allowlisted') | only vetted suppliers | `evaluate_spending_policy` | non-allowlisted requires owner qualification | No | — |
| Anti-order-splitting (24h, per supplier) | prevent splitting to dodge cap | `evaluate_spending_policy` | sum + proposed > $150 → block | No | — |
| Owner approval required to reserve funds | prevent self-authorizing spend | `process_owner_decision` | rejected PO → status rejected | No | **Yes (only path)** |
| Exact-once capture/settlement | avoid double revenue/inventory | `settle_capture` + webhook claim | duplicate event → acknowledged, not reprocessed | N/A | No |
| Tenant identity server-derived | prevent cross-tenant access | `api/deps.py get_merchant_context` | 403 when non-demo + no auth | No | — |
| AI never approves/pays/refunds/raises caps | guardrail | `agent/tools.py`, prompt | tool rejected/blocked | No | — |
| Demo mode gate | fail-closed in production | `get_merchant_context` | 403 when DEMO_MODE=false | N/A | — |

> Note: AI **recommendations** are informational; **enforcement** is deterministic backend code. Do not confuse them.

---

# 8. AI Architecture and Tool Permissions

- **Provider chain:** `FallbackProvider` (`agent/provider.py`) → Gemini (`services/gemini/client.py`, `gemini-3.8-flash`) primary, OpenRouter (`services/openrouter/client.py`, `openrouter/free`) fallback; sticky per request; Gemini primary on new request.
- **Orchestration:** `AgentService.run` (`agent/service.py`): builds `contents` (history + message), calls provider `chat(request, tools)`, parses function calls, executes **only** registered/validated tools, appends function responses, loops ≤ 6; sanitizes errors; never executes a tool directly from the adapter.
- **Tool permissions (read vs write):**
  - Read: `get_dashboard_summary`, `get_inventory`, `get_sales_velocity`, `calculate_reorder_plan`, `compare_suppliers`, `check_spend_policy`.
  - Write (guarded): `create_purchase_order_draft` (explicit request only, unapproved), `request_approval` (surfaces only).
  - **Never:** approve, pay, refund, raise caps, modify allowlist, or bypass validation.
- **Profiles:** `standard` (all), `inventory_only` (`get_inventory` only). Server-defined; client cannot supply an allowlist.
- **Display:** compact answer + metric cards + expandable supporting details (M13.0.8 polish). No chain-of-thought exposed.

---

# 9. PayPal Integration

- **Client:** `services/paypal_client.py` — REST wrapper (httpx), **cached access token**, sandbox base URL (config `paypal_base_url` default `https://api-m.sandbox.paypal.com`), `PayPalAuthError`/`PayPalAPIError`.
- **Config (env, never printed):** `paypal_mode`, `paypal_client_id`, `paypal_client_secret`, `paypal_webhook_id`, `paypal_base_url`, `app_base_url`.
- **Flow:** create order → approval URL → buyer approves → capture → settle; webhook `PAYMENT.CAPTURE.COMPLETED`/`DENIED` verified via `services/paypal_webhook.py` (signature + webhook id) and processed idempotently.
- **Dependency:** `get_paypal_client` in `api/deps.py` (fresh per request, always closed).
- **Not implemented:** PayPal refunds, disputes.

---

# 10. System Architecture and Dependencies

- **Frontend:** React 18, TypeScript, Vite (`frontend/package.json`); no router, no CSS framework, no state library (React hooks). Design tokens in `frontend/src/index.css`; app styling in `frontend/src/App.css`. API layer with runtime validators (`api/client.ts`, `api/types.ts`).
- **Backend:** FastAPI async; `asyncpg` pool (`db/connection.py`); Pydantic v2; layered endpoints→services→repositories; CORS configured for the Vite frontend; migrations runner (`db/migrate.py`, advisory lock, `schema_migrations` checksums).
- **Database:** PostgreSQL (Supabase-compatible). Two connection modes: `DATABASE_URL` (runtime, transaction pooler) and `DIRECT_URL` (migrations, session mode).
- **AI providers:** Google Gemini SDK (google-genai), OpenRouter via httpx. Config: `GEMINI_API_KEY/GEMINI_MODEL`, `OPENROUTER_API_KEY/OPENROUTER_MODEL`.
- **External services:** PayPal REST API (Sandbox).
- **Background tasks:** none/scheduler. `release_expired_reservations` runs opportunistically inside `create_online_order`.
- **Deployment/config:** env-driven (`backend/.env`); `DEMO_MODE`, `DEMO_MERCHANT_ID`, `APP_BASE_URL`, `MIGRATE_ALLOW_INSECURE_LOCAL`.

---

# 11. Current UX Problems

1. **Cockpit loads everything up-front**, and backend-offline produces many repeated, unhelpful error cards ("Cannot reach backend (…)").
2. **No public storefront** — "customer checkout" is a merchant action, which may confuse a reviewer expecting a storefront.
3. **Procurement tab mixes** draft creation with approvals; policy-block feedback is textual and can be missed.
4. **Reorder plan** exists in backend but has no dedicated visual surface (only via AI/procurement).
5. **Terminology** ("spend envelope", "landed cost", "allowlist", "reorder point", "trust tier") is dense for a small retailer; needs plain-language tooltips.
6. **Statuses are inconsistently emphasized** (PO status, order status, webhook states) — need strong visual badges.
7. **The AI answer quality varies**; the compact card layout already helps, but "metric card" heuristic derivation can feel arbitrary.
8. **No empty-state guidance** for empty inventory/suppliers/orders.
9. **Confirmations:** PO approval is high-stakes but the copy is generic.
10. **No global "what changed" activity feed** (audit trail exists in DB but isn't surfaced).

---

# 12. Screen-by-Screen UX Improvement Opportunities (recommendations only — NOT implemented)

- **Shell:** show an always-visible backend status; lazy-load tab data; collapse errors into one banner with retry-all.
- **Cockpit:** make the **spend envelope the hero metric** (big gauge + "left to spend this week"); place reorder-alert SKUs front and center; add a "view all" drill-down.
- **POS:** clearer success path; show updated stock inline; separate "simulator" labeling so it's not mistaken for a real POS app.
- **PayPal checkout:** explain "this opens PayPal (sandbox) in a new tab"; guide the merchant through approve → capture; show status clearly (awaiting approval / captured).
- **Procurement:** dedicated "Create PO" vs "Approvals inbox"; make policy blockers visual (pass/block chips per rule); show envelope impact of a proposed PO before submit; approval should be prominent and require explicit confirm.
- **AI Command Centre:** keep the compact answer + collapsible details; add an explicit "draft created — awaiting owner approval" callout with a direct link to the approvals inbox; consider a "no data" state.

---

# 13. Feature Trade-off Analysis

| Feature | Business value | Complexity | UX benefit | Risks | Limitation | Essential? |
|---|---|---|---|---|---|---|
| Spend envelope | High | Med | High | stale if ledger drift | read-only | Yes |
| AI Command Centre | High | High | High | provider variance, hallucination risk | guarded read-mostly | Yes (flagship) |
| PayPal checkout | High | High | Med | sandbox-only, merchant-initiated | no storefront | Yes |
| POS simulator | High | Low | High | simulate-only | not a real POS | Yes |
| Procurement + approvals | High | High | High | concurrency/locking | no fulfillment state | Yes |
| Inventory grid | High | Low | High | — | read-only | Yes |
| Supplier matrix | Med | Low | Med | — | read-only | Med |
| Refunds/disputes | High | High | High | payment risk | **not implemented** | Pending |
| Production auth | High | Med | Low | — | **not implemented** | Pending |

**Do NOT modify during a visual redesign (functionality-coupled):** the approval decision flow, the spend-envelope numbers, the AI tool/argument validation, the PayPal capture/settlement, the tenant-scoping dependency, and the deterministic policy checks. Changing their presentation is fine (e.g., clearer badges); changing their logic/validation is not.

---

# 14. Critical Functionality That Must Be Preserved

- Backend-enforced spend envelope, per-order ceiling, allowlist, and anti-splitting.
- Owner-approval-as-single-path-to-reserve-funds.
- Server-derived tenant identity + demo gate.
- PayPal create/capture/settle + verified webhooks + idempotent settlement.
- AI guarantee: never approves/pays/refunds/raises caps; strict argument validation; explicit-draft gate.
- Read-only tool profiles and the "never invent values" prompt rules.
- `AgentChatResponse` semantics consumed by the frontend (`status`, `tool_calls`, `blocked`, approval fields).

---

# 15. Incomplete Features and Known Limitations

- **Refunds/returns:** schema-only (NOT IMPLEMENTED).
- **Disputes:** schema-only (NOT IMPLEMENTED).
- **Public storefront:** NOT IMPLEMENTED (checkout is merchant-initiated).
- **Production authentication:** NOT IMPLEMENTED (`DEMO_MODE` fixed tenant).
- **Fulfillment:** "ordered → received" and real supplier payment NOT implemented.
- **Background jobs:** no scheduler; reservation cleanup is opportunistic.
- **Demo data:** one merchant, effectively one fully-seeded flagship product (`prod_blue_vase`).

---

# 16. Recommended Visual Redesign Priorities

1. **Hero spend envelope + reorder alerts** on the Cockpit.
2. **Clear approval inbox** with strong pending/approved/rejected visual states.
3. **AI Command Centre** stays the flagship but should visually bridge to the approvals inbox.
4. **Guided PayPal checkout** flow with explicit step states.
5. **Unified status language** (badges, tooltips, consistent terminology).
6. **Empty/offline states** designed, not raw error cards.

---

## Appendix — Assumptions & Verification Notes

- AGENTS.md (the "Operating Blueprint") was flagged/quarantined by the scanner; this audit is based on **actual code**, not that file.
- Fe live AI/output and PayPal sandbox calls were made; code paths were read statically. Live behavior of `rest(`, webhooks, and AI answers should be confirmed against a running environment.
- Exact PO status/payment_status enums (from `chk_po_status`/`chk_po_payment_status` in migration 001): status ∈ draft / pending_approval / ordered / received / reconciled / rejected / cancelled; payment_status ∈ unpaid / reserved / paid_sandbox / paid_demo / failed. `is_illustrative` default TRUE indicates seed/demo POs.
- `fetchReorderPlan` exists in the frontend client but no UI consumer was found; treat as "backend-backed, not surfaced".
