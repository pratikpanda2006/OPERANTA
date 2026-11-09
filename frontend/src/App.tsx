import React, { useState, useEffect, useRef, useCallback } from 'react';
import { checkBackendHealth } from './api/client';
import { HealthResponse, ConnectionStatus } from './api/types';
import './App.css';

/**
 * App Component — MarginGuard Foundational Landing Shell
 * 
 * Milestone 5: Frontend/Backend Live Communication
 * - Connects to the FastAPI backend at runtime
 * - Validates backend health payload against strict runtime schema
 * - Displays actual HTTP response status codes
 * - Manages loading, connected, and error states with safe overlapping request abortion
 */
export const App: React.FC = () => {
  const [status, setStatus] = useState<ConnectionStatus>('loading');
  const [healthData, setHealthData] = useState<HealthResponse | null>(null);
  const [statusCode, setStatusCode] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  // AbortController ref to safely cancel overlapping or stale requests (Refinement 3)
  const abortControllerRef = useRef<AbortController | null>(null);

  const performHealthCheck = useCallback(async () => {
    // Cancel any previous in-flight request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setStatus('loading');
    setErrorMessage(null);

    try {
      const result = await checkBackendHealth(controller.signal);
      setHealthData(result.data);
      setStatusCode(result.statusCode);
      setStatus('connected');
      setLastChecked(new Date().toLocaleTimeString());
    } catch (err: unknown) {
      // Ignore errors caused by intentional request abortion
      if (err instanceof DOMException && err.name === 'AbortError') {
        return;
      }
      const message = err instanceof Error ? err.message : 'Unknown communication error';
      setStatus('error');
      setErrorMessage(message);
      setStatusCode(null);
    }
  }, []);

  useEffect(() => {
    performHealthCheck();

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [performHealthCheck]);

  return (
    <div className="app-container">
      {/* Top Header Navigation */}
      <header className="app-header">
        <div className="brand-wrapper">
          <div className="brand-icon" aria-hidden="true">🛡️</div>
          <span className="brand-name">MarginGuard</span>
        </div>
        <div className="demo-pill">
          <span className="demo-dot" aria-hidden="true"></span>
          <span>Pilot Merchant: <strong>Northstar Living</strong></span>
        </div>
      </header>

      {/* Hero Section */}
      <main className="hero-card">
        {/* Dynamic Connection Status Badge */}
        {status === 'loading' && (
          <div className="status-badge status-loading" role="status">
            <span className="spinner-indicator" aria-hidden="true"></span>
            <span>Connecting to Backend API...</span>
          </div>
        )}

        {status === 'connected' && (
          <div className="status-badge status-connected" role="status">
            <span className="pulse-indicator" aria-hidden="true"></span>
            <span>Backend Connected (HTTP {statusCode})</span>
          </div>
        )}

        {status === 'error' && (
          <div className="status-badge status-error" role="status">
            <span className="error-indicator" aria-hidden="true"></span>
            <span>Backend Offline</span>
          </div>
        )}

        <h1 className="hero-title">MarginGuard</h1>
        <p className="hero-tagline">Your entire store. One intelligent agent.</p>

        {/* Live Backend Metadata when Connected */}
        {status === 'connected' && healthData && (
          <div className="backend-details">
            <div className="backend-pills">
              <span className="backend-pill">
                Service: <strong>{healthData.service}</strong>
              </span>
              <span className="backend-pill">
                Version: <strong>v{healthData.version}</strong>
              </span>
              <span className="backend-pill">
                Pilot: <strong>{healthData.pilot_merchant}</strong>
              </span>
              {lastChecked && (
                <span className="backend-pill">
                  Synced: <strong>{lastChecked}</strong>
                </span>
              )}
            </div>
          </div>
        )}

        {/* Error Details & Retry Button when Disconnected */}
        {status === 'error' && (
          <div className="error-box" role="alert">
            <p className="error-message">{errorMessage}</p>
            <button
              className="retry-button"
              onClick={performHealthCheck}
            >
              🔄 Retry Connection
            </button>
          </div>
        )}
      </main>

      {/* Operational Loop Preview */}
      <section className="flow-card" aria-label="Autonomous Operational Loop">
        <div className="flow-title">Autonomous Supervised Loop</div>
        <div className="flow-steps">
          <span className="flow-step">1. Understand</span>
          <span className="flow-arrow" aria-hidden="true">→</span>
          <span className="flow-step">2. Decide</span>
          <span className="flow-arrow" aria-hidden="true">→</span>
          <span className="flow-step">3. Explain</span>
          <span className="flow-arrow" aria-hidden="true">→</span>
          <span className="flow-step">4. Request Approval</span>
          <span className="flow-arrow" aria-hidden="true">→</span>
          <span className="flow-step">5. Execute</span>
          <span className="flow-arrow" aria-hidden="true">→</span>
          <span className="flow-step">6. Learn</span>
        </div>
      </section>

      {/* Architecture Baseline Grid */}
      <section className="info-grid" aria-label="Environment Baseline">
        <article className="info-card">
          <h3>📦 Store Baseline</h3>
          <p>
            Configured for hybrid retail operations with shared omnichannel inventory.
          </p>
          <ul className="specs-list">
            <li>1 Brick-and-mortar storefront</li>
            <li>1 Online digital storefront</li>
            <li>~100 Active retail SKUs</li>
            <li>3 Primary suppliers (A, B, C)</li>
          </ul>
        </article>

        <article className="info-card">
          <h3>⚡ Technical Foundation</h3>
          <p>
            Lightweight, high-performance frontend environment ready for dashboard modules.
          </p>
          <ul className="specs-list">
            <li>React 18 + TypeScript</li>
            <li>FastAPI async backend</li>
            <li>Strict runtime schema validation</li>
            <li>Safe request cancellation on retry</li>
          </ul>
        </article>
      </section>

      {/* Minimal Footer */}
      <footer className="app-footer">
        <p>MarginGuard Foundation • Milestone 5 • Supervised Autonomous Retail</p>
      </footer>
    </div>
  );
};

export default App;
