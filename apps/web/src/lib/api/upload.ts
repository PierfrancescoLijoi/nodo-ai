import { ApiError } from './client';

type Fetch = typeof fetch;
export type UploadMode = 'local' | 'azure';

interface UploadOptions {
  url: string;
  audio: Blob;
  mode: UploadMode;
  fetchImpl?: Fetch;
  onProgress?: (fraction: number) => void;
  singlePutLimit?: number;
  blockSize?: number;
}

/** Azure Blob: one request up to 256 MiB, otherwise blocks of 4 MiB and a block list (docs/API-V1.md). */
const AZURE_SINGLE_PUT_LIMIT = 256 * 1024 * 1024;
const AZURE_BLOCK_SIZE = 4 * 1024 * 1024;

export const blockId = (index: number): string => btoa(index.toString().padStart(8, '0'));

const query = (url: string, suffix: string) => `${url}${url.includes('?') ? '&' : '?'}${suffix}`;

async function put(
  fetchImpl: Fetch,
  url: string,
  body: Blob,
  headers?: Record<string, string>,
): Promise<void> {
  // The link is the credential (local one-time link, Azure SAS): no Authorization header goes with it.
  const response = await fetchImpl(url, { method: 'PUT', body, ...(headers ? { headers } : {}) }).catch(
    () => {
      throw new ApiError('Audio upload failed', 0, 'upload_failed');
    },
  );
  if (!response.ok) throw new ApiError('Audio upload failed', response.status, 'upload_failed');
}

export async function uploadAudioToUrl({
  url,
  audio,
  mode,
  fetchImpl = (...args) => fetch(...args),
  onProgress,
  singlePutLimit = AZURE_SINGLE_PUT_LIMIT,
  blockSize = AZURE_BLOCK_SIZE,
}: UploadOptions): Promise<void> {
  onProgress?.(0);
  if (mode === 'local') {
    await put(fetchImpl, url, audio);
    onProgress?.(1);
    return;
  }
  if (audio.size <= singlePutLimit) {
    await put(fetchImpl, url, audio, { 'x-ms-blob-type': 'BlockBlob' });
    onProgress?.(1);
    return;
  }

  const ids: string[] = [];
  for (let offset = 0, index = 0; offset < audio.size; offset += blockSize, index += 1) {
    const id = blockId(index);
    ids.push(id);
    await put(
      fetchImpl,
      query(url, `comp=block&blockid=${encodeURIComponent(id)}`),
      audio.slice(offset, offset + blockSize),
    );
    onProgress?.(Math.min(0.99, (offset + Math.min(blockSize, audio.size - offset)) / audio.size));
  }
  const xml = `<BlockList>${ids.map((id) => `<Latest>${id}</Latest>`).join('')}</BlockList>`;
  await put(fetchImpl, query(url, 'comp=blocklist'), new Blob([xml], { type: 'application/xml' }), {
    'Content-Type': 'application/xml',
  });
  onProgress?.(1);
}
