type Fetch = typeof fetch;

export interface ApiClientOptions {
  baseUrl: string;
  getAccessToken: (forceRefresh?: boolean) => Promise<string>;
  fetchImpl?: Fetch;
  /** Default timeout for one attempt. */
  timeoutMs?: number;
  /** Called once the token was refused twice (a refresh did not help): the session is over. */
  onUnauthorized?: () => Promise<void> | void;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
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

export interface RequestOptions {
  headers?: Record<string, string>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface ApiClient {
  get<T>(path: string, options?: RequestOptions): Promise<T>;
  post<T>(path: string, body: unknown, options?: RequestOptions): Promise<T>;
  patch<T>(path: string, body: unknown, options?: RequestOptions): Promise<T>;
  delete(path: string, options?: RequestOptions): Promise<void>;
  /** The body of an authenticated GET as a file (downloads). */
  blob(path: string, options?: RequestOptions): Promise<Blob>;
}

const DEFAULT_TIMEOUT_MS = 20_000;
const MAX_ATTEMPTS = 3;
const MAX_WAIT_MS = 10_000;
/** The API says to retry reads on these (docs/API-V1.md, "Overload and outages"). */
const BUSY = new Set([429, 502, 503, 504]);

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** The wait the server asked for, plus jitter so a crowd of clients does not return at the same instant. */
export function retryDelayMs(response: Response, attempt: number, random: () => number): number {
  const asked = Number(response.headers.get('Retry-After'));
  const base = Number.isFinite(asked) && asked > 0 ? asked * 1000 : 500 * 2 ** attempt;
  return Math.min(MAX_WAIT_MS, base + random() * 250);
}

async function attempt(
  fetchImpl: Fetch,
  url: string,
  init: RequestInit,
  timeoutMs: number,
  outer?: AbortSignal,
) {
  const controller = new AbortController();
  const onOuterAbort = () => controller.abort();
  outer?.addEventListener('abort', onOuterAbort);
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchImpl(url, { ...init, signal: controller.signal });
  } catch {
    const cancelled = outer?.aborted ?? false;
    const timedOut = !cancelled && controller.signal.aborted;
    throw new ApiError(
      cancelled ? 'Request cancelled' : timedOut ? 'Request timed out' : 'Network request failed',
      0,
      cancelled ? 'cancelled' : timedOut ? 'timeout' : 'network_error',
    );
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener('abort', onOuterAbort);
  }
}

async function failure(response: Response): Promise<ApiError> {
  const envelope = (await response.json().catch(() => undefined)) as ErrorEnvelope | undefined;
  return new ApiError(
    envelope?.error?.message ?? 'API request failed',
    response.status,
    envelope?.error?.code ?? 'http_error',
    envelope?.error?.request_id ?? response.headers.get('X-Request-Id') ?? undefined,
  );
}

export function createApiClient({
  baseUrl,
  getAccessToken,
  fetchImpl = (...args) => fetch(...args),
  timeoutMs = DEFAULT_TIMEOUT_MS,
  onUnauthorized,
  sleep = realSleep,
  random = Math.random,
}: ApiClientOptions): ApiClient {
  const send = async (
    method: string,
    path: string,
    body: unknown,
    options: RequestOptions = {},
    accept = 'application/json',
  ): Promise<Response> => {
    // A write may be sent again only when it carries an idempotency key (the API's rule).
    const repeatable = method === 'GET' || !!options.headers?.['Idempotency-Key'];
    let refreshed = false;
    for (let tries = 0; ; tries += 1) {
      const token = await getAccessToken(refreshed);
      const response = await attempt(
        fetchImpl,
        `${baseUrl}${path}`,
        {
          method,
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: accept,
            ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
            ...options.headers,
          },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
          cache: 'no-store',
        },
        options.timeoutMs ?? timeoutMs,
        options.signal,
      );
      if (response.status === 401) {
        if (!refreshed) {
          refreshed = true;
          continue;
        }
        await onUnauthorized?.();
      }
      if (BUSY.has(response.status) && repeatable && tries + 1 < MAX_ATTEMPTS) {
        await sleep(retryDelayMs(response, tries, random));
        continue;
      }
      return response;
    }
  };

  const json = async <T>(
    method: string,
    path: string,
    body?: unknown,
    options?: RequestOptions,
  ): Promise<T> => {
    const response = await send(method, path, body, options);
    if (!response.ok) throw await failure(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  };

  return {
    get: (path, options) => json('GET', path, undefined, options),
    post: (path, body, options) => json('POST', path, body ?? {}, options),
    patch: (path, body, options) => json('PATCH', path, body, options),
    delete: (path, options) => json<void>('DELETE', path, undefined, options),
    async blob(path, options) {
      const response = await send('GET', path, undefined, options, '*/*');
      if (!response.ok) throw await failure(response);
      return response.blob();
    },
  };
}
