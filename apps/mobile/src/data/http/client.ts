type Fetch = typeof fetch;

interface ClientOptions {
  baseUrl: string;
  getAccessToken: (forceRefresh?: boolean) => Promise<string>;
  fetchImpl?: Fetch;
  timeoutMs?: number;
  onUnauthorized?: () => Promise<void> | void;
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string };
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ApiClient {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body: unknown): Promise<T>;
  patch<T>(path: string, body: unknown): Promise<T>;
}

async function fetchWithTimeout(
  fetchImpl: Fetch,
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchImpl(url, { ...init, signal: controller.signal });
  } catch {
    throw new ApiError(
      controller.signal.aborted ? 'Request timed out' : 'Network request failed',
      0,
      controller.signal.aborted ? 'timeout' : 'network_error',
    );
  } finally {
    clearTimeout(timeout);
  }
}

export function createApiClient({
  baseUrl,
  getAccessToken,
  fetchImpl = fetch,
  timeoutMs = 20_000,
  onUnauthorized,
}: ClientOptions): ApiClient {
  const request = async <T>(method: string, path: string, body?: unknown, retried = false): Promise<T> => {
    const token = await getAccessToken(retried);
    const response = await fetchWithTimeout(
      fetchImpl,
      `${baseUrl}${path}`,
      {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/json',
          ...(body ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      },
      timeoutMs,
    );
    if (response.status === 401 && !retried) return request(method, path, body, true);
    if (response.status === 401) await onUnauthorized?.();
    const payload = (await response.json().catch(() => undefined)) as ErrorEnvelope | T | undefined;
    if (!response.ok) {
      const envelope = payload as ErrorEnvelope | undefined;
      throw new ApiError(
        envelope?.error?.message ?? 'API request failed',
        response.status,
        envelope?.error?.code ?? 'http_error',
        envelope?.error?.request_id,
      );
    }
    return payload as T;
  };
  return {
    get: <T>(path: string) => request<T>('GET', path),
    post: <T>(path: string, body: unknown) => request<T>('POST', path, body),
    patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  };
}
