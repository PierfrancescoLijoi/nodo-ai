import { ApiError, createApiClient } from '../client';

const response = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

describe('api client', () => {
  it('sends bearer auth and parses JSON', async () => {
    const fetchImpl = jest.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      response({ ok: true }),
    ) as jest.MockedFunction<typeof fetch>;
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      getAccessToken: async () => 'token',
      fetchImpl,
    });
    await expect(client.get('/test')).resolves.toEqual({ ok: true });
    expect(fetchImpl.mock.calls[0]?.[1]?.headers).toMatchObject({ Authorization: 'Bearer token' });
  });

  it('maps an error envelope', async () => {
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      getAccessToken: async () => 'token',
      fetchImpl: () => response({ error: { code: 'bad', message: 'No', request_id: 'req-1' } }, 422),
    });
    await expect(client.get('/test')).rejects.toEqual(
      expect.objectContaining({ status: 422, code: 'bad', requestId: 'req-1' }),
    );
  });

  it('refreshes and retries once after 401', async () => {
    const fetchImpl = jest
      .fn()
      .mockImplementationOnce(() => response({}, 401))
      .mockImplementationOnce(() => response({ ok: true }));
    const getAccessToken = jest.fn(async (force?: boolean) => (force ? 'new' : 'old'));
    const client = createApiClient({ baseUrl: 'https://api.example.com', getAccessToken, fetchImpl });
    await expect(client.get('/test')).resolves.toEqual({ ok: true });
    expect(getAccessToken).toHaveBeenLastCalledWith(true);
  });

  it('signs out after two 401 responses', async () => {
    const onUnauthorized = jest.fn(async () => undefined);
    const client = createApiClient({
      baseUrl: 'https://api.example.com',
      getAccessToken: async () => 'token',
      fetchImpl: () => response({}, 401),
      onUnauthorized,
    });
    await expect(client.get('/test')).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('maps timeouts and network failures', async () => {
    const timeout = createApiClient({
      baseUrl: 'https://api.example.com',
      getAccessToken: async () => 'token',
      timeoutMs: 1,
      fetchImpl: (_input, init) =>
        new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new Error('aborted')))),
    });
    await expect(timeout.get('/slow')).rejects.toMatchObject({ code: 'timeout' });
    const network = createApiClient({
      baseUrl: 'https://api.example.com',
      getAccessToken: async () => 'token',
      fetchImpl: async () => {
        throw new Error('offline');
      },
    });
    await expect(network.get('/offline')).rejects.toMatchObject({ code: 'network_error' });
  });
});
