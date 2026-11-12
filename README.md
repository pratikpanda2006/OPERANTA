# OPERANTA
### Intelligence Behind Every Operation

**Your AI-powered agentic commerce assistant for smarter retail operations.**

[Live Demo](https://operanta-frontend.onrender.com) | [Source Code](https://github.com/pratikpanda2006/OPERANTA)

---

## Overview

Retailers constantly balance inventory, customer demand, supplier pricing, purchasing budgets, and operational decisions.

OPERANTA brings these activities together in one intelligent operational cockpit.

It uses AI to understand business context, analyse inventory and supplier information, recommend procurement actions, and prepare purchase-order drafts — while enforcing financial rules through deterministic backend logic.

**The goal:** Move from disconnected business data to intelligent, controlled action.

## Key Features

- **Omnichannel Inventory Intelligence:** Monitor stock across in-store and online channels, identify low-stock products, and track reorder thresholds.
- **Supplier Evaluation:** Compare supplier prices, freight costs, reliability, lead times, and minimum order quantities.
- **AI Command Centre:** Use natural-language prompts to analyse business data and generate procurement recommendations.
- **Financial Guardrails:** Enforce spending envelopes, per-order ceilings, supplier allowlists, and backend validation.
- **Human-in-the-Loop Procurement:** Prepare purchase-order drafts and route them through merchant approval workflows.
- **PayPal Sandbox Checkout:** Create checkout orders, obtain buyer approval, capture payments, and reconcile payment events.

## How It Works

OPERANTA combines AI reasoning with deterministic business rules.

1. The application collects inventory, sales, supplier, and spending information.
2. Google Gemini analyses the available business context.
3. The AI recommends operational actions and can prepare purchase-order drafts following explicit user requests.
4. Backend rules independently validate spending limits and procurement constraints.
5. The merchant retains control over purchase-order approval.
6. Customer checkout is handled through PayPal Sandbox integration.

**AI recommends. Backend rules enforce. Merchants stay in control.**

## Technology Stack

| Layer | Technologies |
|---|---|
| Frontend | React, TypeScript, Vite |
| Interactive tables | AG Grid Community |
| Charts | AG Charts |
| Backend | Python, FastAPI |
| Database | Supabase, PostgreSQL |
| Primary AI | Google Gemini |
| AI fallback | OpenRouter |
| Payments | PayPal Developer Platform |
| Hosting | Render |

## PayPal Integration

OPERANTA integrates the PayPal Developer Platform using the free Sandbox environment.

The payment workflow includes:

- **OAuth 2.0:** Backend authentication with PayPal.
- **Orders API:** Creates customer checkout orders.
- **Buyer Approval:** Redirects buyers to PayPal's approval experience.
- **Capture API:** Captures approved payments.
- **Webhooks:** Processes verified payment events.
- **Idempotent Settlement:** Helps prevent duplicate payment processing.

Payment settlement updates the corresponding internal order, payment records, revenue ledger, and inventory movements.

**Important:** PayPal Sandbox is used for customer-facing checkout demonstrations. Supplier purchase orders use a separate merchant approval workflow; supplier payments are not automatically executed.

No real customer funds are processed in the demonstration.

## AI Integration

OPERANTA uses **Google Gemini** as its primary AI provider and **OpenRouter** as a configured fallback.

The AI Command Centre uses function calling to support:

- Inventory and sales analysis.
- Reorder recommendations.
- Supplier comparisons.
- Spending-policy analysis.
- Purchase-order draft creation.

The AI cannot independently approve purchase orders, execute payments, modify spending limits, or bypass backend validation.

## Architecture

```text
React + TypeScript Frontend
           |
           v
      FastAPI Backend
           |
     +-----+------+
     |     |      |
  Supabase Gemini PayPal
     |     |      |
     |  OpenRouter|
     |  Fallback  |
     |
     v
Business Logic
     |
Financial Guardrails
     |
Merchant Approval
```

## Running Locally

### Prerequisites

- Python 3.11+
- Node.js and npm
- PostgreSQL / Supabase database
- Google Gemini API key
- OpenRouter API key (optional fallback)
- PayPal Developer account with Sandbox credentials

### 1. Clone the repository

```bash
git clone https://github.com/pratikpanda2006/OPERANTA.git
cd OPERANTA
```

### 2. Backend setup

Create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r backend/requirements.txt
```

Configure the backend environment using the project's deployment documentation and backend configuration.

Required configuration includes database connectivity, AI provider credentials, PayPal Sandbox credentials, and authentication settings.

Start the backend:

```powershell
uvicorn app.main:app --app-dir backend --reload
```

Backend health endpoint:

`http://localhost:8000/health`

### 3. Frontend setup

```powershell
cd frontend
npm install
```

Create `frontend/.env.local`:

```env
VITE_API_BASE_URL=http://localhost:8000
```

Start the frontend:

```powershell
npm run dev
```

Open `http://localhost:5173`.

### 4. PayPal Sandbox setup

1. Sign in to the PayPal Developer Dashboard.
2. Create a Sandbox REST application.
3. Obtain Sandbox Client ID and Secret.
4. Configure the credentials in the backend environment.
5. Configure webhook details if testing webhook settlement.
6. Use a Sandbox buyer account to test checkout.

Never expose API secrets in frontend code or commit `.env` files.

## Live Demo

- **Frontend:** https://operanta-frontend.onrender.com
- **Backend:** https://operanta.onrender.com
- **Repository:** https://github.com/pratikpanda2006/OPERANTA

### Testing Instructions

1. Open the hosted frontend.
2. Register or access the demo workspace.
3. Explore inventory, stock-health charts, and supplier information.
4. Open the AI Command Centre and ask questions about inventory and procurement.
5. Explore purchase-order drafts and merchant approval workflows.
6. Open POS & Sales to explore PayPal Sandbox checkout.

A PayPal Sandbox buyer account is required to complete a Sandbox checkout.

## Sponsor Integrations

- **PayPal:** Sandbox checkout and payment processing.
- **AG Grid:** Interactive inventory table.
- **AG Charts:** Inventory and reorder-threshold visualisation.
- **Render:** Frontend and backend deployment.

## Project Creator

Independently conceptualised, designed, developed, integrated, tested, and deployed by **Pratik Panda**.

## Future Scope

- Advanced demand forecasting.
- Multi-location inventory intelligence.
- Deeper supplier analytics.
- Enhanced merchant reporting.
- Additional commerce integrations.

## License

Licensed under the MIT License. See [LICENSE](LICENSE) for details.
