import { describe, expect, it, vi } from 'vitest';

import { blockId, uploadAudioToUrl } from './upload';

const ok = () => new Response(null, { status: 201 });
const calls = (fetchImpl: ReturnType<typeof vi.fn>) =>
  fetchImpl.mock.calls.map(([url, init]) => ({ url: url as string, init: init as RequestInit }));

describe('uploadAudioToUrl', () => {
  it('local: one PUT of the raw bytes with no Authorization header (the link is the credential)', async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 204 }));
    const progress = vi.fn();
    await uploadAudioToUrl({
      url: 'http://127.0.0.1:8765/v1/local/uploads/abc',
      audio: new Blob(['audio']),
      mode: 'local',
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onProgress: progress,
    });
    const [call] = calls(fetchImpl);
    expect(call?.init.method).toBe('PUT');
    expect(call?.init.headers).toBeUndefined();
    expect(progress.mock.calls.map(([value]) => value)).toEqual([0, 1]);
  });

  it('azure: a small file is one block blob request', async () => {
    const fetchImpl = vi.fn(async () => ok());
    await uploadAudioToUrl({
      url: 'https://st.blob.core.windows.net/audio/m1?sig=x',
      audio: new Blob(['audio']),
      mode: 'azure',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(calls(fetchImpl)).toHaveLength(1);
    expect(calls(fetchImpl)[0]?.init.headers).toEqual({ 'x-ms-blob-type': 'BlockBlob' });
  });

  it('azure: a large file is staged in blocks and committed with a block list', async () => {
    const fetchImpl = vi.fn(async () => ok());
    const progress = vi.fn();
    await uploadAudioToUrl({
      url: 'https://st.blob.core.windows.net/audio/m1?sig=x',
      audio: new Blob(['0123456789']),
      mode: 'azure',
      fetchImpl: fetchImpl as unknown as typeof fetch,
      singlePutLimit: 4,
      blockSize: 4,
      onProgress: progress,
    });
    const made = calls(fetchImpl);
    expect(made.map((call) => call.url)).toEqual([
      `https://st.blob.core.windows.net/audio/m1?sig=x&comp=block&blockid=${encodeURIComponent(blockId(0))}`,
      `https://st.blob.core.windows.net/audio/m1?sig=x&comp=block&blockid=${encodeURIComponent(blockId(1))}`,
      `https://st.blob.core.windows.net/audio/m1?sig=x&comp=block&blockid=${encodeURIComponent(blockId(2))}`,
      'https://st.blob.core.windows.net/audio/m1?sig=x&comp=blocklist',
    ]);
    const list = made[3]?.init.body as Blob;
    expect(await list.text()).toBe(
      `<BlockList>${[0, 1, 2].map((index) => `<Latest>${blockId(index)}</Latest>`).join('')}</BlockList>`,
    );
    expect(made[3]?.init.headers).toEqual({ 'Content-Type': 'application/xml' });
    const reported = progress.mock.calls.map(([value]) => value as number);
    expect(reported.at(-1)).toBe(1);
    expect(reported.slice(0, -1).every((value) => value < 1)).toBe(true);
  });

  it('adds block parameters after an existing query string, and uses a ? when there is none', async () => {
    const fetchImpl = vi.fn(async () => ok());
    await uploadAudioToUrl({
      url: 'https://st/audio/m1',
      audio: new Blob(['abcdef']),
      mode: 'azure',
      fetchImpl: fetchImpl as unknown as typeof fetch,
      singlePutLimit: 1,
      blockSize: 6,
    });
    expect(calls(fetchImpl)[0]?.url).toContain('m1?comp=block');
  });

  it('reports a refused upload as an ApiError', async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 403 }));
    await expect(
      uploadAudioToUrl({
        url: 'https://x',
        audio: new Blob(['a']),
        mode: 'azure',
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    ).rejects.toMatchObject({ code: 'upload_failed', status: 403 });
  });

  it('reports a dropped connection the same way', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('network');
    });
    await expect(
      uploadAudioToUrl({
        url: 'https://x',
        audio: new Blob(['a']),
        mode: 'local',
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    ).rejects.toMatchObject({ code: 'upload_failed', status: 0 });
  });
});

describe('blockId', () => {
  it('is the base64 of an eight digit counter, the same length for every block', () => {
    expect(blockId(0)).toBe(btoa('00000000'));
    expect(blockId(12)).toHaveLength(blockId(0).length);
  });
});
