import { describe, expect, it, vi } from 'vitest';

import { ApiError, createApiClient, retryDelayMs } from './client';

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' }, ...init });

function setup(responses: (Response | Error)[], options: { onUnauthorized?: () => void } = {}) {
  const queue = [...responses];
  const fetchImpl = vi.fn(async () => {
    const next = queue.shift();
    if (!next) throw new Error('no more responses');
    if (next instanceof Error) throw next;
    return next;
  });
  const getAccessToken = vi.fn(async (force?: boolean) => (force ? 'fresh' : 'token'));
  const sleep = vi.fn(async () => undefined);
  const client = createApiClient({
    baseUrl: 'https://api.test',
    getAccessToken,
    fetchImpl: fetchImpl as unknown as typeof fetch,
    sleep,
    random: () => 0,
    ...options,
  });
  return { client, fetchImpl, getAccessToken, sleep };
}

const headerOf = (fetchImpl: ReturnType<typeof setup>['fetchImpl'], call: number, name: string) =>
  ((fetchImpl.mock.calls[call] as unknown as [string, RequestInit])[1].headers as Record<string, string>)[
    name
  ];

describe('createApiClient', () => {
  it('sends the bearer token and returns the JSON body', async () => {
    const { client, fetchImpl } = setup([json({ ok: true })]);
    await expect(client.get('/v1/meetings')).resolves.toEqual({ ok: true });
    expect(headerOf(fetchImpl, 0, 'Authorization')).toBe('Bearer token');
    expect((fetchImpl.mock.calls[0] as unknown as [string])[0]).toBe('https://api.test/v1/meetings');
  });

  it('turns the standard error body into an ApiError with the request id', async () => {
    const { client } = setup([
      json(
        { error: { code: 'not_found', message: 'Resource not found', request_id: 'req-1' } },
        { status: 404 },
      ),
    ]);
    await expect(client.get('/v1/x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      code: 'not_found',
      requestId: 'req-1',
    });
  });

  it('falls back to the X-Request-Id header when the body has none', async () => {
    const { client } = setup([new Response('nope', { status: 500, headers: { 'X-Request-Id': 'hdr-1' } })]);
    await expect(client.get('/v1/x')).rejects.toMatchObject({
      status: 500,
      code: 'http_error',
      requestId: 'hdr-1',
    });
  });

  it('asks for a fresh token once when the first is refused, then succeeds', async () => {
    const { client, getAccessToken, fetchImpl } = setup([json({}, { status: 401 }), json({ ok: 1 })]);
    await expect(client.get('/v1/x')).resolves.toEqual({ ok: 1 });
    expect(getAccessToken).toHaveBeenNthCalledWith(1, false);
    expect(getAccessToken).toHaveBeenNthCalledWith(2, true);
    expect(headerOf(fetchImpl, 1, 'Authorization')).toBe('Bearer fresh');
  });

  it('ends the session when a fresh token is refused too', async () => {
    const onUnauthorized = vi.fn();
    const { client } = setup([json({}, { status: 401 }), json({}, { status: 401 })], { onUnauthorized });
    await expect(client.get('/v1/x')).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('retries a read that was told to wait, honouring Retry-After', async () => {
    const { client, sleep } = setup([
      json({}, { status: 503, headers: { 'Retry-After': '2' } }),
      json({ ok: 1 }),
    ]);
    await expect(client.get('/v1/x')).resolves.toEqual({ ok: 1 });
    expect(sleep).toHaveBeenCalledWith(2000);
  });

  it('gives up after three attempts', async () => {
    const { client, fetchImpl } = setup([
      json({}, { status: 503 }),
      json({}, { status: 503 }),
      json({}, { status: 503 }),
    ]);
    await expect(client.get('/v1/x')).rejects.toMatchObject({ status: 503 });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it('never repeats a write without an idempotency key', async () => {
    const { client, fetchImpl } = setup([json({}, { status: 503 }), json({ ok: 1 })]);
    await expect(client.post('/v1/query', { question: 'q' })).rejects.toMatchObject({ status: 503 });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('repeats a write that carries an idempotency key', async () => {
    const { client, fetchImpl } = setup([json({}, { status: 503 }), json({ id: 'm1' })]);
    await expect(
      client.post('/v1/meetings', {}, { headers: { 'Idempotency-Key': 'k-12345678' } }),
    ).resolves.toEqual({
      id: 'm1',
    });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('reports a dropped connection as a network error', async () => {
    const { client } = setup([new TypeError('failed')]);
    await expect(client.get('/v1/x')).rejects.toMatchObject({ code: 'network_error', status: 0 });
  });

  it('reports a request cancelled by the caller', async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const client = createApiClient({
      baseUrl: 'https://api.test',
      getAccessToken: async () => 't',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const pending = client.get('/v1/x', { signal: controller.signal });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'cancelled' });
  });

  it('times out a request that never answers', async () => {
    const fetchImpl = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const client = createApiClient({
      baseUrl: 'https://api.test',
      getAccessToken: async () => 't',
      fetchImpl: fetchImpl as unknown as typeof fetch,
      timeoutMs: 5,
    });
    await expect(client.get('/v1/x')).rejects.toMatchObject({ code: 'timeout' });
  });

  it('answers 204 with nothing and sends DELETE and PATCH', async () => {
    const { client, fetchImpl } = setup([new Response(null, { status: 204 }), json({ ok: 1 })]);
    await expect(client.delete('/v1/meetings/m1')).resolves.toBeUndefined();
    await expect(client.patch('/v1/x', { a: 1 })).resolves.toEqual({ ok: 1 });
    expect((fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe('DELETE');
    expect((fetchImpl.mock.calls[1] as unknown as [string, RequestInit])[1].body).toBe('{"a":1}');
  });

  it('returns a download as a blob', async () => {
    const { client } = setup([new Response('file-body', { status: 200 })]);
    const blob = await client.blob('/v1/export/j/download');
    expect(await blob.text()).toBe('file-body');
  });

  it('never sends the Authorization header away from the API (no cookies, no cache)', async () => {
    const { client, fetchImpl } = setup([json({})]);
    await client.get('/v1/x');
    expect((fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1].cache).toBe('no-store');
  });
});

describe('retryDelayMs', () => {
  it('uses the server wait, capped, with jitter', () => {
    const fast = new Response(null, { headers: { 'Retry-After': '3' } });
    expect(retryDelayMs(fast, 0, () => 0)).toBe(3000);
    expect(retryDelayMs(fast, 0, () => 1)).toBe(3250);
    expect(retryDelayMs(new Response(null, { headers: { 'Retry-After': '999' } }), 0, () => 0)).toBe(10_000);
  });

  it('backs off exponentially when the server does not say', () => {
    const none = new Response(null);
    expect([0, 1, 2].map((attempt) => retryDelayMs(none, attempt, () => 0))).toEqual([500, 1000, 2000]);
  });
});

describe('ApiError', () => {
  it('is an Error with its fields', () => {
    const error = new ApiError('m', 409, 'conflict', 'r');
    expect(error).toBeInstanceOf(Error);
    expect([error.status, error.code, error.requestId]).toEqual([409, 'conflict', 'r']);
  });
});
