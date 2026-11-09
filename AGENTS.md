# AGENTS.md — MarginGuard Operating Blueprint & Agent Guidelines

---

## 1. Product Identity & Overview

- **Product Name:** MarginGuard
- **Tagline:** *Your entire store. One intelligent agent.*
- **Target Audience:** Independent US retailers managing hybrid operations (brick-and-mortar storefronts alongside online commerce).
- **Core Mission:** Provide autonomous yet strictly supervised retail operations, inventory intelligence, and disciplined procurement protection so independent store owners can preserve their profit margins without managing multiple disjointed systems.

### What MarginGuard Is & Is Not
- **MarginGuard IS:** An AI-powered retail operations copilot and autonomous procurement guardrail. It observes store health, predicts restock needs, evaluates supplier trade-offs, orchestrates purchase orders within financial guardrails, and drafts refund resolutions with audit trails.
- **MarginGuard IS NOT:**
  - A point-of-sale (POS) hardware system or standalone retail register replacement.
  - An e-commerce website builder or storefront CMS (e.g., Shopify/Squarespace alternative).
  - An unconstrained, generic chatbot that directly executes financial transfers or uncontrolled database mutations.

### Target Demo Business: Northstar Living
To ground all development, seed data, workflows, and test cases:
- **Profile:** Independent boutique home goods and lifestyle retailer based in the US.
- **Footprint:** 1 physical brick-and-mortar boutique store, 1 online storefront.
- **Scale:** ~100 distinct active SKUs/products across home décor, kitchenware, and textiles.
- **Suppliers:** 3 primary suppliers (Supplier A, Supplier B, Supplier C).
- **Team:** 2 in-store employees + 1 store owner (acting as the primary approver).

### Core Autonomous Operational Flow
Every automated or AI-assisted operational action in MarginGuard strictly follows the 6-stage lifecycle:
```
[ Understand ] ──> [ Decide ] ──> [ Explain ] ──> [ Request Approval ] ──> [ Execute ] ──> [ Learn ]
```
1. **Understand:** Ingest POS transactions, online orders, inventory thresholds, returns, supplier pricing, and lead-time signals.
2. **Decide:** Apply business logic, inventory forecasting formulas, and supplier evaluation heuristics within budget constraints.
3. **Explain:** Articulate the rationale in plain, accessible language with transparent metrics (e.g., unit cost, landed cost, shipping time, margin impact, risk flags).
4. **Request Approval:** Escalate any action that involves money movement, purchase orders, or policy overrides to the merchant owner for explicit sign-off.
5. **Execute:** Upon verified human sign-off, trigger idempotent backend API calls, record ledger events, and notify downstream channels.
6. **Learn:** Record transaction outcomes, delivery reliability, dispute resolutions, and merchant overrides to refine future recommendations.

---

## 2. Technology Stack & Environment

| Layer | Technology | Purpose & Architectural Role |
| :--- | :--- | :--- |
| **Frontend** | React 18+ with Vite | Fast, responsive single-page application (SPA) providing dashboards, approvals, and simulators. |
| **Backend** | Python 3.11+ with FastAPI | High-performance asynchronous REST API handling business logic, guardrails, policy enforcement, and integrations. |
| **Database** | PostgreSQL via Supabase | Relational data persistence, row-level security (RLS), ACID transactions, and relational integrity. |
| **AI Engine** | Gemini (via function/tool calling) | Structured reasoning, natural language retail query processing, and schema-validated tool invocation. |
| **Payments** | PayPal REST API (Sandbox) & PayPal Payouts | Real payment processing, capture, order authorization, webhook ingestion, refund flows, and supplier payments via PayPal Payouts (target integration). |
| **Data Tables** | AG Grid Community / Enterprise | Rich, high-density transactional grids for stock levels, orders, supplier comparisons, and audit logs. |
| **Deployment** | Render | Production and staging hosting for frontend static assets and backend web services. |
| **API Testing** | Postman / Pytest | Contract testing, integration suites, and repeatable verification of backend endpoints. |

---

## 3. Core Functional Modules

### 1. Overview Dashboard
- **Sales & Margin Metrics:** Daily/weekly revenue, gross sales, estimated product contribution margins, and return rates across channels. Estimated contribution calculations must strictly use only known costs; unknown shipping, labour, overhead, and tax costs must be flagged as missing, never silently treated as zero.
- **Inventory Alerts:** Real-time warnings for low-stock SKUs, stockout velocity risks, and replenishment deadlines.
- **Spend Envelope Gauge:** Visual real-time indicator tracking spend for the active demo week versus the allocated $500 Agent Spend Envelope cap (not an unspecified monthly budget).
- **Pending Approvals Queue:** High-priority cards surfacing pending purchase orders, supplier authorizations, and refund requests awaiting owner action.

### 2. AI Command Centre
- **Natural Language Retail Operations:** Allows the merchant to query store status (e.g., *"What were my top 3 margin products this week?"*, *"Do we need to reorder linen throws?"*, *"Draft a PO for ceramic mugs"*).
- **Decision Transparency:** Presents clear explanations, calculated landed costs, and reason codes alongside any AI recommendation.
- **Action Sandbox:** Gemini never directly alters state; it generates structured action proposals (`propose_purchase_order`, `flag_dispute_for_review`) that enter the approval pipeline.

### 3. Sales & Orders
- **Omnichannel Unified Stream:** Ingests orders from both the physical store and the online storefront into a consolidated database schema.
- **POS Simulator:** Lightweight in-browser terminal allowing testing of in-person retail sales, cash/card handling, and immediate stock deductions.
- **Online Checkout:** Real PayPal Sandbox checkout integration processing customer payments, calculating tax/shipping, and emitting webhooks.

### 4. Inventory & Procurement
- **Shared Unified Stock:** Live inventory synchronization across in-store and online channels to prevent double-selling.
- **Replenishment Forecasting:** Reorder-point calculations based on historical sales velocity and supplier lead times.
- **Supplier Matrix & Comparison:** Multi-factor supplier comparison evaluating:
  - Unit purchase price
  - Shipping rates and shipping methods
  - Estimated lead times (days to dock)
  - Minimum Order Quantities (MOQ)
  - Historical delivery reliability score
- **Purchase Order Engine & Supplier Payments:** PO draft generation, line item breakdowns, lifecycle management (`draft` -> `pending_approval` -> `ordered` -> `received` -> `reconciled`), and supplier payments via PayPal Payouts as a target integration. Verify Sandbox availability before claiming it works; if unavailable, retain the purchase order and envelope reservation and clearly show "Not sent (demo)".

### 5. Returns & Disputes
- **Omnichannel Return Processing:** Item returns originating online or at the store counter.
- **Dispute Evidence & Audit Vault:** Collection of dispute rationales, tracking information, customer correspondence, and condition photos.
- **Refund Orchestration:** Safe, backend-verified refund execution via PayPal Sandbox with strict duplicate-refund prevention.

---

## 4. Procurement & Spending Rules

All procurement calculations and order requests must strictly respect these hard business rules:

1. **Agent Spend Envelope ($500 Cap):**
   - The total cumulative spend for Northstar Living applies strictly to the **active demo week** (not an unspecified monthly budget) and cannot exceed **$500.00 USD**.
   - Any order that would push cumulative expenditure over the $500 active demo week cap is blocked by the backend policy engine.
2. **Per-Order Landed Cost Limit ($150 Cap):**
   - No individual purchase order may exceed **$150.00 USD** in total landed cost (`unit_cost * quantity + shipping + handling + taxes`).
   - Orders exceeding $150 are automatically rejected or required to be split only if allowed by operational policy.
3. **Supplier Allowlist & Trust Tiers:**
   - **Supplier A:** Pre-approved & Allowlisted (Home décor & ceramics).
   - **Supplier C:** Pre-approved & Allowlisted (Kitchenware & dining).
   - **Supplier B / New Suppliers:** Not allowlisted. Generating a purchase order for Supplier B or any new supplier requires **explicit owner approval** to register and qualify the vendor.
4. **Anti-Order-Splitting Guardrail (24-Hour Rule):**
   - To prevent bypassing the $150 per-order threshold, the system strictly forbids creating multiple purchase orders directed to the **same supplier within any rolling 24-hour window** if their combined value exceeds $150.
5. **Holistic Supplier Comparison Principle:**
   - **Cheapest does NOT automatically mean best.**
   - Decision scoring must weigh reliability and stockout risk against unit price savings. A supplier with 10% lower cost but 3x lead time or low reliability score must be flagged with a trade-off warning.
6. **Illustrative Data Tagging:**
   - All supplier catalog data, delivery reliability ratings, and demo supplier listings must be explicitly flagged and labelled as **"Illustrative Demo Data"** in both UI grids and backend metadata.

---

## 5. Security & System Integrity Rules

Security in MarginGuard is enforced through defense-in-depth, strict privilege separation, and tamper-resistant backend logic.

1. **Backend-Enforced Spending Policy:**
   - Spending limits, spend envelope tracking, and order approval validation must **always execute on the backend (FastAPI/Postgres)**.
   - Frontend validation and Gemini prompt constraints are UX conveniences only; neither is trusted for enforcement.
2. **No Direct Financial Execution by AI:**
   - The Gemini AI agent **never** possesses API keys or privileges to trigger payment gateways, PayPal payouts, or funds transfers.
   - Gemini only yields structured tool outputs proposing draft operations.
3. **Mandatory Human-in-the-Loop for Financial Actions:**
   - Any state change involving financial expenditure, purchase order placement, or customer refunds requires explicit, cryptographic or session-authenticated **owner approval**.
4. **Rigorous Tool Call Schema Validation:**
   - Every tool call or function call generated by Gemini must be validated against strict Pydantic models on the backend before any processing occurs. Malformed or unrecognized calls are rejected immediately.
5. **Multi-Tenant Data Isolation (`merchant_id`):**
   - Every single database table, query, session, and audit record must be strictly scoped by `merchant_id`. Cross-tenant data leakage is zero-tolerance.
6. **Secret Hygiene:**
   - PayPal Client Secrets, Supabase service keys, Gemini API keys, and server-side encryption keys must **never be bundled into frontend client code**, committed to version control, or returned via public API responses.
7. **Idempotency, Concurrency & Webhook Safeguards:**
   - All payment endpoints, purchase order submissions, and webhook consumers must require unique **Idempotency Keys** (`Idempotency-Key` HTTP header / database unique constraints).
   - Require PayPal webhook signature verification, event ID deduplication, and idempotent event processing to prevent duplicate processing or double-crediting.
8. **Double-Refund & Inventory Movement Protection:**
   - Every refund must link directly to an existing, non-refunded transaction ID, tracking `amount_refunded` against `original_amount`.
   - Inventory deductions and additions must use atomic database transactions (`SELECT ... FOR UPDATE` or transactional RPCs) to prevent race conditions.
9. **Untrusted Input Sanitization & Prompt Injection Defense:**
   - Customer messages, return dispute notes, and supplier shipment comments must be treated as **strictly untrusted raw data**.
   - These fields must be escaped and sanitized before being presented to Gemini or rendered in the DOM, and must never be interpreted as system instructions or override commands.

---

## 6. Engineering Standards & Best Practices

1. **Simplicity & Maintainability First:**
   - Prefer simple, readable, modular architectures over heavy, over-engineered abstractions.
   - Write clean, type-annotated Python code (FastAPI + Pydantic) and typed, idiomatic React code (clean component decomposition, hooks, clear prop types).
2. **Mentorship & Accessibility:**
   - Code should be documented and explained thoughtfully so that early-career and beginner developers can understand the "why" behind patterns, database migrations, and state management.
3. **Truthfulness in Verification & Testing:**
   - **Never claim a test passed unless it was actually executed and verified in the environment.**
   - Include actual command terminal outputs when reporting test or verification results.
4. **No Synthetic / Hallucinated Credentials:**
   - Never invent mock credentials, fake API tokens, or silent fallbacks that disguise missing configurations. If environment variables are missing, fail fast with a clear diagnostic message.
5. **Strict Scope Discipline:**
   - Do not build unapproved features, extra bells and whistles, or premature microservices.
   - Build exactly what is requested for the current task.
6. **No Fake Payment Shortcuts:**
   - Integrate the real PayPal REST Sandbox API with authentic request-response cycles, real client-server token exchange, and real webhook/capture verification. Never fake a payment confirmation dialog with a hardcoded static `true`.
7. **Clear Architectural Boundaries:**
   - Maintain a clean boundary between the React frontend (presentation, local UI state, UX forms) and the FastAPI backend (validation, business rules, database access, external API communication).
8. **Automated Testing Baseline:**
   - Maintain unit and integration tests for all core business logic: spend envelope tracking, landed cost calculation, 24-hour split-order detection, and refund eligibility.
9. **Demo Viability Over Edge-Case Breadth:**
   - Prioritize a rock-solid, fully functioning end-to-end demo flow for Northstar Living over peripheral edge features.

---

## 7. Collaborative Working Style & Operational Protocol

When working on MarginGuard, every contributor and agent must abide by the following operational protocol:

1. **Explain the Plan Before Writing Code:**
   - Detail the proposed design, affected components, and step-by-step strategy before modifying or creating files.
2. **Focused & Atomic Changes:**
   - Keep changes tightly scoped to the specific objective.
   - List every file created, updated, or removed in each step.
3. **Run Real Checks & Show Output:**
   - Execute relevant linters, type checks, build commands, or test suites directly in the environment and present the unedited terminal output.
4. **Honest Failure Reporting:**
   - If a build breaks, a package fails to resolve, or an API request returns an error, state the exact error clearly and debug methodically. Never mask or hide issues.
5. **Await Developer Pacing:**
   - Complete the current task, provide verification evidence, and wait for explicit confirmation or the next instruction before proceeding.

