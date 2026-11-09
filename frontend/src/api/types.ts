/**
 * HealthResponse contract returned by the backend GET /health endpoint.
 */
export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  pilot_merchant: string;
}

/**
 * Runtime validation of the health-check JSON payload.
 * Verifies that all expected fields exist, are non-empty strings,
 * and match the expected schema before setting connected state.
 */
export function isHealthResponse(data: unknown): data is HealthResponse {
  if (typeof data !== 'object' || data === null) {
    return false;
  }

  const record = data as Record<string, unknown>;

  return (
    typeof record.status === 'string' &&
    record.status.trim().length > 0 &&
    typeof record.service === 'string' &&
    record.service.trim().length > 0 &&
    typeof record.version === 'string' &&
    record.version.trim().length > 0 &&
    typeof record.pilot_merchant === 'string' &&
    record.pilot_merchant.trim().length > 0
  );
}

export type ConnectionStatus = 'loading' | 'connected' | 'error';

export interface HealthCheckResult {
  data: HealthResponse;
  statusCode: number;
}

