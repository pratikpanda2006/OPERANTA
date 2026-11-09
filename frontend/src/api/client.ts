import { HealthCheckResult, isHealthResponse } from './types';

/**
 * Resolves the backend API base URL from Vite environment variables,
 * defaulting to the local FastAPI development URL (http://127.0.0.1:8000).
 */
export function getApiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
}

/**
 * Checks the operational health of the MarginGuard FastAPI backend.
 * 
 * Features:
 * - Accepts an AbortSignal to safely cancel stale or overlapping in-flight requests
 * - Retrieves and returns the actual HTTP response status code
 * - Validates JSON payload at runtime using isHealthResponse
 */
export async function checkBackendHealth(signal?: AbortSignal): Promise<HealthCheckResult> {
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}/health`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
      },
      cache: 'no-cache',
      signal,
    });
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
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

  // Runtime validation of JSON schema (Refinement 1)
  if (!isHealthResponse(rawJson)) {
    throw new Error(
      `Invalid health payload schema from backend (HTTP ${statusCode}). Expected non-empty { status, service, version, pilot_merchant }`
    );
  }

  return {
    data: rawJson,
    statusCode,
  };
}

