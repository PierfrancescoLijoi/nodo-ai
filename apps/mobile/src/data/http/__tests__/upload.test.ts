import { uploadAudioToUrl } from '../upload';

const fetchMock = () =>
  jest.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(null, { status: 201 }),
  ) as jest.MockedFunction<typeof fetch>;

describe('audio upload', () => {
  it('uses one unauthenticated PUT for a local upload', async () => {
    const fetchImpl = fetchMock();
    const progress = jest.fn();
    await uploadAudioToUrl({
      url: 'http://127.0.0.1/upload',
      audio: new Blob(['hello']),
      mode: 'local',
      fetchImpl,
      onProgress: progress,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0]?.[1]).toMatchObject({ method: 'PUT' });
    expect((fetchImpl.mock.calls[0]?.[1] as RequestInit).headers).toBeUndefined();
    expect(progress).toHaveBeenLastCalledWith(1);
  });

  it('uses Put Blob for a small Azure upload', async () => {
    const fetchImpl = fetchMock();
    await uploadAudioToUrl({
      url: 'https://blob.test/item?sig=x',
      audio: new Blob(['small']),
      mode: 'azure',
      fetchImpl,
    });
    expect(fetchImpl.mock.calls[0]?.[1]).toMatchObject({ headers: { 'x-ms-blob-type': 'BlockBlob' } });
  });

  it('stages fixed-size blocks and commits their ordered ids', async () => {
    const fetchImpl = fetchMock();
    const progress = jest.fn();
    await uploadAudioToUrl({
      url: 'https://blob.test/item?sig=x',
      audio: new Blob(['abcdefghij']),
      mode: 'azure',
      fetchImpl,
      singlePutLimit: 5,
      blockSize: 4,
      onProgress: progress,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    expect(String(fetchImpl.mock.calls[0]?.[0])).toContain('&comp=block&blockid=');
    expect(String(fetchImpl.mock.calls[2]?.[0])).toContain('&comp=block&blockid=');
    expect(String(fetchImpl.mock.calls[3]?.[0])).toContain('&comp=blocklist');
    expect(await (fetchImpl.mock.calls[3]?.[1]?.body as Blob).text()).toMatch(
      /^<BlockList><Latest>.*<\/Latest><Latest>.*<\/Latest><Latest>.*<\/Latest><\/BlockList>$/,
    );
    expect(progress).toHaveBeenLastCalledWith(1);
  });
});
